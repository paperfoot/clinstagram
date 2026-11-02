# Brand art provenance

`logo.png` and `social-preview.png` were made on 2 November 2026.

The lettering was generated with Codex's native image generation (GPT-Image-2) through
`codex exec --skip-git-repo-check -s workspace-write`, round 1 of 1, four options from one
brief (`r1/PROMPT.txt`). Style reference: the chunky extruded block lettering of the Minecraft
Java Edition title logo, the same treatment as engram-cli's logo.

| Option | Material | Chosen |
|---|---|---|
| `logo_a` | Glazed terracotta in a violet → magenta → orange → gold sunset gradient | Yes |
| `logo_b` | White quartz with a magenta-to-orange trim | |
| `logo_c` | Blackstone with glowing pink and orange veins | |
| `logo_d` | Pink and coral concrete with glowstone tops | |

A won on reading as Instagram at a glance and staying legible on both GitHub themes.
The round-1 originals are kept outside git (`r1/*.png` is ignored); their checksums are below.

`logo.png` is option A trimmed to its artwork, scaled to 1600 px wide and quantised with
pngquant. `social-preview.png` (1280 x 640, GitHub's card size) places it on a radial gradient
(#2A1036 to #0B0710) with the tagline in SF Pro Display Semibold and the install line in SF Mono
(#FF9A5A), composed with ImageMagick.

## Checksums (SHA-256)

```
42c4854147d0f31e3a5b62b788f6fb648e6a78c84b1575d143b2d50665526104  r1/logo_a.png
d7196fbcffa40f857f958da8ec834c68d282bfaf3284406f0a8b1748793150e5  r1/logo_b.png
15df1f4b2309e54969220d880c19b3a37de10de421ef87fee235686e86305052  r1/logo_c.png
275afdc13b232d0c5f2666073d2790e98e1b17010f817d932792957dace5ff05  r1/logo_d.png
cbcfe09a4a7309c7bd34b0e473a11ee5f00103084ac5d39ba1cc321a298153c8  logo.png
39df4e5150e3e3f18fc8af6934e06b1ec72a02a65519eadd878c45969dfec316  social-preview.png
```
