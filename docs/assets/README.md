# Visual assets

The homepage artwork is an AI-generated conceptual illustration, not a product screenshot, proof of formal verification, or a photograph of a deployed system. The SVG diagrams are editable, code-authored explanations grounded in the public implementation. They are not execution traces. The intake example uses synthetic data.

## Assets

- `hero.png`: generated using the built-in image generation tool; supplied pixels are preserved.
- `execution-contract.svg`: neural, symbolic, verification, and evidence responsibilities.
- `residual-intake.svg`: a synthetic language-to-structure example with explicit unresolved evidence.

The project visuals in the current version are offered under AGPL-3.0-only, without third-party company marks or endorsement. Earlier versions through commit `dd539c8` were offered under Apache-2.0; the prior grant for those versions remains effective.

## Hero generation prompt

```text
Use case: stylized-concept.
Asset type: premium widescreen GitHub README hero banner for an open-source engineering neuro-symbolic agent architecture.
Primary request: create an exceptionally polished, restrained technical visual that communicates language/ideas becoming a constrained, evidence-checked engineered structure. Concept art, not product screenshot or real infrastructure deployment.
Composition: wide cinematic banner, ideally around 2400x960. Generous left text area (55%); refined 3D technical sculpture on the right (45%). Safe margins at least 6%.
Scene: almost black midnight navy #071320, subtle fine blueprint grid and a soft cyan glow; no busy space scene.
Subject on right: an elegant floating isometric bridge-like truss structure made of frosted optical glass and dark brushed metal, an open lattice of neural points/curved signal threads resolves through one translucent rectangular verification plane into rigid orderly geometry and a few clear aligned evidence layers. Premium product-launch rendering, precise architectural detailing, restrained cyan #35D8D0 and electric-blue #6EA8FE highlights, a tiny amber unresolved node. Symbolic visual only. No humanoid robots, no brains, no generic circuit head, no trophy.
Text verbatim on left, excellent Swiss sans serif typography and hierarchy:
small tracked eyebrow: "NEURO-SYMBOLIC ENGINEERING"
large two-line title: "Engineering" then "Domain Agent"
subtitle: "Make verification part of execution."
small bottom line: "MODEL PROPOSES  /  SYMBOLS CONSTRAIN  /  EVIDENCE GOVERNS"
Use only this exact text, no other captions, logos, badges, fake UI or data. Ivory white main title, cool grey subtitle, subtle cyan eyebrow. Crisp readable lettering. Atmospheric yet highly legible. The central engineering sculpture must remain secondary to the title and clearly separated; no overlap with text. No watermark. No company identity, certification claim or performance metric. Opaque background.
```

## Maintenance

Keep diagrams self-contained, with accessible titles and descriptions, no scripts, no embedded external resources, and readable labels at GitHub README width. Update the release integrity manifest after any asset change. Release scanning only performs bounded structural checks; it does not verify the semantic truth of every pixel or diagram label.
