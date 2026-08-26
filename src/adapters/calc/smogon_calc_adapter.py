"""Smogon damage-calc adapter (deterministic layer, Node IPC).

Encapsulates the polyglot boundary between the Python core and the Node.js
``@smogon/calc`` subsystem. The Node side is a thin request/response worker
(``node_calc/calc_server.js``) that reads one JSON request per line on stdin
and writes one JSON response per line on stdout.

If the calc backend is ever replaced (Rust, C++, remote service), only this
adapter changes — no domain or service code touches Node/JS details.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from src.domain.exceptions import CalcEngineError
from src.domain.models import CalcRequest, DamageResult, PokemonSet, SpeedComparison


class SmogonCalcAdapter:
    """Concrete :class:`~src.domain.interfaces.CalcEngineAdapter` over Node IPC.

    A single long-lived Node subprocess is reused across calls. Access is
    serialized with a lock, because the stdin/stdout protocol is strictly
    request/response and not safe for concurrent interleaving.
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
            stderr = self._drain_stderr(process)
            self._reap()
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
            # The parser deliberately keeps `species` as the stable roster
            # identity even after a Mega Evolution / in-battle forme change
            # (see showdown_parser.record_forme) — the last OBSERVED
            # appearance is sent separately so the Node engine can use its
            # real stats when its installed dex recognizes it, falling back
            # to `species` itself otherwise (see calcEngine.buildPokemon).
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

    def calculate(self, request: CalcRequest) -> DamageResult:
        """Run a single deterministic damage calculation via the Node engine."""
        response = self._rpc(
            {
                "cmd": "calc",
                "gen": request.gen or self._gen,
                "attacker": self._mon_payload(request.attacker),
                "defender": self._mon_payload(request.defender),
                "move": request.move,
                "field": request.field,
            }
        )
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
                "field": request.field,
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

    def forme_resolves(self, gen: int, species: str) -> bool:
        """Whether the installed @smogon/calc's dex has real data for this
        exact forme string (e.g. "Staraptor-Mega") — used to decide whether
        a stat-approximation caveat is still warranted for it."""
        response = self._rpc({"cmd": "formeResolves", "gen": gen, "species": species})
        return bool(response.get("result", {}).get("resolves", False))

    @staticmethod
    def _speed_conditions(request: CalcRequest, trick_room: bool) -> list[str]:
        """Human-readable labels for the modifiers applied to the speed check."""
        field = request.field or {}
        labels: list[str] = []
        if field.get("attackerTailwind"):
            labels.append(f"Tailwind ({request.attacker.species})")
        if field.get("defenderTailwind"):
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
        return labels
