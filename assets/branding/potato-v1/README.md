# Potato Logo

用于活动项目封面和应用品牌素材。未修改前端代码。

- `potato-icon.png`：1254 × 1254，蓝色方形底，建议上传至活动页面。
- `potato-mark-transparent.png`：1254 × 1254，RGBA 透明底，适合介绍材料和界面排版。

设计概念：金色土豆轮廓呼应 Potato，内部带两个端点的 P 形路径表达旅行规划；蓝色呼应现有界面的强调色。两张图均由内置 image_gen 工具生成，属于 PNG 位图素材。透明版本已经检查 alpha 通道，外部背景透明。生成版本的轮廓与留白存在细微差异。

## 原始生成提示词

```text
Use case: logo-brand
Asset type: final standalone square app logo for Potato, an AI conversational travel planning assistant; intended for a project avatar in an AI Works hackathon.
Primary request: Design an original, refined, memorable flat logo combining a potato silhouette and a travel route subtly forming an uppercase P. One logo only.
Subject: a warm golden yellow potato silhouette, slightly asymmetric and gently diagonally tilted, confidently simple organic contour. Inside it, a single thick cobalt-blue route line subtly reads as uppercase P, with two circular route endpoints integrated into the geometry. Make the route feel purposeful and friendly, with generous negative space. The potato is recognizable from its organic outline rather than texture or facial features. The silhouette should be distinctive and clean, crafted like a strong contemporary app identity, not a stock map pin.
Scene/backdrop: uniform solid cobalt blue #315CCE full square canvas, no rounded tile drawn inside; the upload UI will apply rounding if needed.
Color palette: golden yellow #F5C66A mark, cobalt blue #315CCE internal route matching backdrop. Exactly two flat solid colors.
Style/medium: professional vector-like 2D logo, crisp precise contours, bold economical geometry, no outlines, no gradients, no lighting, no depth.
Composition/framing: single large centered icon occupying about 62% of canvas width and height, generous even safe margins, optically balanced. Square image 1024 by 1024.
Text: none. Do not add Potato or any other lettering.
Constraints: identifiable at 32 pixels; internal route thick enough to survive reduction; no separate dots or decorative elements outside the silhouette.
Avoid: eyes, mouth, face, mascot limbs, emoji, leaves, sprouts, sparkles, AI stars, robots, circuit patterns, generic location pin, globe, airplane, extra symbols, photorealism, 3D, bevels, shadows, texture, mockup, watermark, presentation board.
```

## 透明底编辑提示词

```text
Use case: background-extraction
Asset type: transparent-background Potato logo mark for reuse.
Input image: the supplied square blue-and-gold Potato logo is the edit target.
Primary request: remove ONLY the blue square exterior backdrop around the golden potato, making the exterior actually transparent. Preserve the entire golden potato contour exactly, its shape, position, scale, and the blue P-shaped travel route and both circular endpoints INSIDE the potato. The internal blue P route remains opaque cobalt blue #315CCE; it must NOT be removed as background. Keep the square canvas and original generous spacing. Refine the gold and the internal blue into flat solid colors, eliminating faint grain or mottling. Golden fill #F5C66A. Sharp clean anti-aliased edges. No new elements, no lettering, no shadows, no background replacement, no white fill. Actual alpha transparency outside the potato silhouette.
Use the recent full square logo showing a golden potato with the blue P route as the edit target. Ignore any tiny blue thumbnail; preserve the logo from the full generated image.
```
