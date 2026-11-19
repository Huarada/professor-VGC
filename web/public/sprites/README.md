# Pixel sprites

The decorative Pikachu and Mewtwo GIFs on the React landing page were obtained
from the public Pokemon Showdown Gen 5 animated sprite set:

- `https://play.pokemonshowdown.com/sprites/gen5ani/pikachu.gif`
- `https://play.pokemonshowdown.com/sprites/gen5ani/mewtwo.gif`
- `https://play.pokemonshowdown.com/sprites/gen5/pikachu.png`
- `https://play.pokemonshowdown.com/sprites/gen5/mewtwo.png`

They are bundled locally so the page does not need a third-party image request
at render time. The CSS attack beams and particles are original interface code.
The sprites are decorative; reduced-motion users see static PNGs without attack effects.

## Roster ribbon

The scrolling ribbon uses Gen 5 animated sprites from the same public set
(`https://play.pokemonshowdown.com/sprites/gen5ani/<species>.gif`) for Charizard, Gengar,
Garchomp, Lucario, Metagross, Gyarados, Tyranitar, Volcarona and Hydreigon, bundled as
`roster-<species>.gif`. The 8-bit Poke Ball (`../pokeball-pixel.svg`) and the attack
projectiles are original SVG drawn for this page.
