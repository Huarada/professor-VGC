"""Damage-calc adapter over Node IPC to ``@smogon/calc`` (one JSON line in,
one out; ``node_calc/calc_server.js``). Replacing the engine touches only
this module.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from src.domain.exceptions import CalcEngineError
from src.domain.models import (
    CalcField,
    CalcRequest,
    DamageResult,
    MoveInfo,
    PokemonSet,
    SideField,
    SpeedComparison,
)


class SmogonCalcAdapter:
    """``CalcEngineAdapter`` over one long-lived Node subprocess; calls are
    serialized with a lock (the protocol is strictly request/response).
    """

    def __init__(
        self,
        server_script: str | Path,
        node_binary: str = "node",
        gen: int = 9,
        timeout_seconds: float = 20.0,
    ) -> None:
        # Resolve to an absolute path: the subprocess runs with cwd set to the
        # script's directory, so a relative arg would double-resolve.
        self._script = Path(server_script).resolve()
        self._node_binary = node_binary
        self._gen = int(gen)
        self._timeout = float(timeout_seconds)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._species_names: dict[int, list[str]] = {}

        if not self._script.exists():
            raise CalcEngineError(f"Calc server script not found: {self._script}")

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        try:
            self._process = subprocess.Popen(  # noqa: S603 - trusted local script
                [self._node_binary, str(self._script)],
                cwd=str(self._script.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Node writes UTF-8; Windows would otherwise decode with cp1252 and crash
                # the reader thread on any non-ASCII name.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise CalcEngineError(
                f"Failed to launch Node calc process ({self._node_binary}): {exc}"
            ) from exc
        return self._process

    def _rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one request line and read one response line (thread-safe)."""
        with self._lock:
            process = self._ensure_process()
            if process.stdin is None or process.stdout is None:  # pragma: no cover
                raise CalcEngineError("Node calc process has no stdio pipes")
            try:
                process.stdin.write(json.dumps(payload) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._reap()
                raise CalcEngineError(f"Calc engine pipe broken: {exc}") from exc

            line = self._read_line_with_timeout(process)

        if not line:
            # Kill first: reading stderr of a still-running process blocks
            # forever (it never reaches EOF), turning a failed call into a hang.
            self._reap()
            stderr = self._drain_stderr(process)
            raise CalcEngineError(f"Empty response from calc engine. stderr: {stderr}")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalcEngineError(f"Invalid JSON from calc engine: {line!r}") from exc
        if not isinstance(response, dict):
            raise CalcEngineError(f"Calc engine returned a non-object response: {line!r}")
        if response.get("ok") is False:
            raise CalcEngineError(f"Calc engine error: {response.get('error')}")
        return response

    def _read_line_with_timeout(self, process: subprocess.Popen[str]) -> str:
        """Read a single stdout line, enforcing a timeout via a watchdog."""
        result: dict[str, str] = {}

        def _reader() -> None:
            assert process.stdout is not None
            result["line"] = process.stdout.readline()

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(self._timeout)
        if thread.is_alive():
            self._reap()
            raise CalcEngineError(f"Calc engine timed out after {self._timeout:.1f}s")
        return result.get("line", "")

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> str:
        if process.stderr is None:
            return ""
        try:
            return process.stderr.read() or ""
        except OSError:  # pragma: no cover
            return ""

    def _reap(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:  # pragma: no cover
                pass
            self._process = None

    def close(self) -> None:
        """Terminate the Node subprocess and release resources."""
        with self._lock:
            if self._process is not None:
                try:
                    if self._process.stdin is not None:
                        self._process.stdin.close()
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
                    self._reap()
                finally:
                    self._process = None

    def __enter__(self) -> "SmogonCalcAdapter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _mon_payload(mon: PokemonSet) -> dict[str, Any]:
        payload: dict[str, Any] = {"species": mon.species, "level": mon.level}
        if mon.battle_formes:
            # `species` stays the roster identity after a forme change; the last seen
            # forme is sent too so the engine uses its stats when it knows them.
            payload["battleForme"] = mon.battle_formes[-1]
        if mon.ability:
            payload["ability"] = mon.ability
        if mon.item:
            payload["item"] = mon.item
        if mon.nature:
            payload["nature"] = mon.nature
        if mon.tera_type:
            payload["teraType"] = mon.tera_type
        if mon.status:
            payload["status"] = mon.status
        if mon.evs is not None:
            payload["evs"] = mon.evs.as_dict()
        if mon.ivs is not None:
            payload["ivs"] = mon.ivs.as_dict()
        if mon.boosts:
            payload["boosts"] = dict(mon.boosts)
        return payload

    @staticmethod
    def _side_payload(side: SideField) -> dict[str, bool]:
        return {
            "isTailwind": side.tailwind,
            "isReflect": side.reflect,
            "isLightScreen": side.light_screen,
            "isAuroraVeil": side.aurora_veil,
            "isHelpingHand": side.helping_hand,
            "isFriendGuard": side.friend_guard,
        }

    @classmethod
    def _field_payload(cls, field: CalcField) -> dict[str, Any]:
        """Domain field -> the Node worker's @smogon/calc-shaped field spec."""
        payload: dict[str, Any] = {
            "trickRoom": field.trick_room,
            "attackerSide": cls._side_payload(field.attacker_side),
            "defenderSide": cls._side_payload(field.defender_side),
        }
        if field.weather:
            payload["weather"] = field.weather
        if field.terrain:
            payload["terrain"] = field.terrain
        return payload

    def calculate(self, request: CalcRequest) -> DamageResult:
        """Run a single deterministic damage calculation via the Node engine."""
        payload: dict[str, Any] = {
            "cmd": "calc",
            "gen": request.gen or self._gen,
            "attacker": self._mon_payload(request.attacker),
            "defender": self._mon_payload(request.defender),
            "move": request.move,
            "field": self._field_payload(request.field),
        }
        if request.defender_hp_percent is not None:
            payload["defenderHpPercent"] = request.defender_hp_percent
        response = self._rpc(payload)
        data = response.get("result", {})
        try:
            return DamageResult(
                attacker=request.attacker.species,
                defender=request.defender.species,
                move=request.move,
                damage_rolls=list(data.get("damage", []) or []),
                min_percent=float(data.get("minPercent", 0.0)),
                max_percent=float(data.get("maxPercent", 0.0)),
                ko_chance_text=str(data.get("koChanceText", "")),
                is_ko_guaranteed=bool(data.get("isKoGuaranteed", False)),
                description=str(data.get("desc", "")),
            )
        except (TypeError, ValueError) as exc:
            raise CalcEngineError(f"Malformed calc result: {data!r}") from exc

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        """Deterministically compare final speeds of attacker and defender."""
        response = self._rpc(
            {
                "cmd": "speed",
                "gen": request.gen or self._gen,
                "attacker": self._mon_payload(request.attacker),
                "defender": self._mon_payload(request.defender),
                "field": self._field_payload(request.field),
            }
        )
        data = response.get("result", {})
        atk_spe = int(data.get("attackerSpeed", 0))
        def_spe = int(data.get("defenderSpeed", 0))
        trick_room = bool(data.get("trickRoom", False))
        conditions = self._speed_conditions(request, trick_room)

        if atk_spe == def_spe:
            return SpeedComparison(
                faster=request.attacker.species,
                slower=request.defender.species,
                faster_speed=atk_spe,
                slower_speed=def_spe,
                is_tie=True,
                trick_room=trick_room,
                conditions=conditions,
            )
        # Under Trick Room the SLOWER Pokemon moves first.
        attacker_first = (atk_spe > def_spe) != trick_room
        if attacker_first:
            return SpeedComparison(
                faster=request.attacker.species,
                slower=request.defender.species,
                faster_speed=atk_spe,
                slower_speed=def_spe,
                trick_room=trick_room,
                conditions=conditions,
            )
        return SpeedComparison(
            faster=request.defender.species,
            slower=request.attacker.species,
            faster_speed=def_spe,
            slower_speed=atk_spe,
            trick_room=trick_room,
            conditions=conditions,
        )

    def move_info(self, gen: int, move: str) -> MoveInfo:
        """Static move data from the engine's own dex (category, spread
        target, Protect-family, speed control)."""
        response = self._rpc({"cmd": "moveInfo", "gen": gen, "move": move})
        data = response.get("result", {})
        try:
            return MoveInfo(
                name=str(data.get("name") or move),
                known=bool(data.get("known", False)),
                category=str(data.get("category") or ""),
                is_spread=bool(data.get("isSpread", False)),
                is_protect=bool(data.get("isProtect", False)),
                speed_control=str(data.get("speedControl") or ""),
                speed_drop_stages=int(data.get("speedDropStages", 0) or 0),
            )
        except (TypeError, ValueError) as exc:
            raise CalcEngineError(f"Malformed move info: {data!r}") from exc

    def species_names(self, gen: int) -> list[str]:
        """Every species name the engine's dex knows (cached per generation);
        satisfies :class:`~src.domain.interfaces.SpeciesCatalog`."""
        cached = self._species_names.get(gen)
        if cached is None:
            response = self._rpc({"cmd": "speciesNames", "gen": gen})
            names = response.get("result", {}).get("names")
            if not isinstance(names, list):
                raise CalcEngineError(f"Malformed species list: {response!r}")
            cached = [str(name) for name in names]
            self._species_names[gen] = cached
        return list(cached)

    def forme_resolves(self, gen: int, species: str) -> bool:
        """Whether the engine's dex has stats for this exact forme (e.g. a Mega)."""
        response = self._rpc({"cmd": "formeResolves", "gen": gen, "species": species})
        return bool(response.get("result", {}).get("resolves", False))

    @staticmethod
    def _speed_conditions(request: CalcRequest, trick_room: bool) -> list[str]:
        """Human-readable labels for the modifiers applied to the speed check."""
        field = request.field
        labels: list[str] = []
        if field.attacker_side.tailwind:
            labels.append(f"Tailwind ({request.attacker.species})")
        if field.defender_side.tailwind:
            labels.append(f"Tailwind ({request.defender.species})")
        if request.attacker.status == "par":
            labels.append(f"paralysis ({request.attacker.species})")
        if request.defender.status == "par":
            labels.append(f"paralysis ({request.defender.species})")
        if request.attacker.item == "Choice Scarf":
            labels.append(f"Choice Scarf ({request.attacker.species})")
        if request.defender.item == "Choice Scarf":
            labels.append(f"Choice Scarf ({request.defender.species})")
        if trick_room:
            labels.append("Trick Room")
        if field.weather:
            labels.append(f"weather {field.weather}")
        return labels
