# Pawline UI rules

Pawline is a small desktop monitor. Users should be able to identify the selected
task, compare requested and observed models, and read the last input-cache record
without resizing the card. GTK and Qt implement the same information structure.

## Priorities and layout

1. Work progress and model observation are independent. The provider context
   row shows work progress; the response row shows model evidence. A Claude
   startup setting does not prove that an actual request matched the response.
2. Show the selected task and actual response model first. The title uses the
   full content width. Request/start settings stay in the compact model detail
   instead of repeating the model in the overview.
3. Show automatic tracking versus direct selection in each provider context.
   Keep the window pin control and its current state visible in the toolbar.
4. Cache details use one proportional bar: reused input and remaining input.
   The concise legend reads 적중 / 미적중, with exact input-token counts available in tooltips. Output tokens do not enter this bar. Cache writes belong to remaining input.
   Missing counts show a waiting state, never a fabricated zero-percent graph.
5. Details fit their contents. Switching from a cache graph or long explanation
   to one short sentence must release the previous minimum height.

| Element | Rule |
| --- | --- |
| Main card | 320 px wide, 12 px padding, 12 px corner radius |
| Global toolbar | Pawline, pin icon and sun/moon toggle; two 28 px buttons with 18 px vectors |
| Provider context | Provider, tracking mode, work progress; 20 px high |
| Task selector | Full content width, 28 px high |
| Text alignment | Toolbar name, provider header, task title and field labels share the same inset |
| Response row | 48 px caption, 8 px gap, actual model, 88 px model-detail control |
| Cache row | Same 48 px caption and value column; percentage, trailing record age |
| Interactive rows | 28 px high, 8 px content inset where appropriate |
| Vertical spacing | 4 px within a provider; separator has 7 px above and below |
| Typography | Pretendard; 14 px/600 task title, 13 px/500 model and provider, 13 px/600 percentage, 12 px/400 metadata, 18 px/600 graph value |
| Text colors | Primary for titles and percentages, secondary for models and providers, muted for labels and time; semantic colors only for status |
| Direction icons | Shared 12 px vector chevrons, aligned to the same right edge on all three rows |
| Cache graph | 8 px bar; 8 px group spacing; a fixed subtle gradient from the shared accent palette |

`fluff_monitor/presentation.py` owns the geometry, font scale and dark/light
palettes consumed by GTK and Qt. Main cards and popups share the same surface,
border, text hierarchy, and accent colors. The two themes share semantic roles,
not unrelated per-widget color overrides. Missing usage keeps the caption and
value columns in place; it displays a waiting state instead of a false zero.
The graph value is a modest data emphasis,
not a separate large headline. The gradient palette is fixed within each theme; only the filled length represents the cache fraction. A redundant total-input sentence is omitted from the visible chart. Graphs and normal details share the same title,
content margins, corners and dismissal rules.

Toolbar controls are direct actions, with no duplicate overflow menu. The pin
uses an outlined tilted shape when off and a filled upright shape when on.
The sun switches to light mode and the moon switches to dark mode. Tooltips and
accessible names describe the next action. Both native frontends use the same
SVG geometry and colors, without depending on platform icon fonts.

Qt explicitly selects the bundled variable font's `wght` axis after style
resolution. In the tested backend, nominal weight 600 alone still painted the
Regular outline; an A/B native render confirmed the explicit axis fixed it.
See [Qt's variable-font API](https://doc.qt.io/qt-6/qfont.html#setVariableAxis).

Long titles may extend on hover while the card stays fixed; the extension must
avoid the pet and remain on screen. Model details expose requested versus actual
values, with a short explanation only when observation is unavailable. The
cache graph is the last input record, not evidence that a server cache remains
warm or an estimate of money saved.

## Design references reviewed

These references informed specific decisions, rather than being mixed into a
new visual style. The official pages and their UI examples were examined in the
Codex in-app browser on 2026-09-29.

- [Toss writing principles](https://toss.tech/article/21022): concise wording must
  preserve useful context. The cache row says “입력 캐시” with the record age;
  its accessible description and details identify the last input record and
  separate the metric from its limits.
- [Slack Block Kit design](https://docs.slack.dev/concepts/designing-with-block-kit/):
  show concise information first, use interaction for details, and pair status
  colors with words. This supports the separate cache row and clickable status.
- [Apple toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars):
  group related controls with recognizable symbols. Theme and pin are direct
  global actions; pet actions remain in the pet's context menu.
- [Meta's Facebook redesign](https://engineering.fb.com/2020/05/08/web/facebook-redesign/)
  and [StyleX at scale](https://engineering.fb.com/2026/01/12/web/css-at-scale-with-stylex/):
  shared theme and component rules reduce visual drift. The 2020 article is a
  historical light/dark example, not a claim about the latest Facebook UI.
- [Notion page design update](https://www.notion.com/en-gb/blog/updating-the-design-of-notion-pages):
  consistent spacing and grouping improve scanability. Both providers now use
  the same row rhythm and column positions.

## Verification boundary

`tests/verify_activity_ui.py` renders real GTK widgets in an isolated X server.
`tests/verify_qt_ui.py` renders Qt widgets and checks interactions with Qt's
offscreen backend. They check paired columns, long titles, panel size, popup
placement, full detail text, graph-to-empty transitions, short-popup height, tracking mode, visible pin state, theme changes, and Escape dismissal. The comparison
gallery shows these native renders, not a separate HTML recreation of Pawline.

Qt checks performed on Linux do not prove native Windows startup or resolve
Windows Smart App Control/code-signing restrictions. UI changes require only the
pet process to reload; the observer, activity collector, and Codex app remain
running.
