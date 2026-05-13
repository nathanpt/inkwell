# Workflow Diagram Skill Prompt

Analyze the codebase and create a single self-contained HTML page that documents workflows between packages and components in the app.

Requirements:
- Place all components/packages on the page, visually grouped by layer or concern (e.g. UI, core, data, external)
- Provide a sidebar of clickable actions like "Invite new user" or "todesktop build" or {insert other flows here}
- When clicked, highlight the flow between packages with animated paths/arrows and annotate how things are passed between each package to complete the action
- Dim unrelated components so the active flow stands out
- All flow data is driven from an embedded JSON array, where each flow has: name, description, and an ordered list of steps with `from`, `to`, `label`, and `detail` fields
- Show a detail panel with per-step explanations when a flow is selected

## Original prompt (for reference)

Create a single-page HTML that documents workflows between packages and components in the app. Have all the components/packages on the page and I can click on different actions like "Invite new user" or "todesktop build" or {insert other flows here} and then it will highlight the flow between the packages and annotate how things are passed between each package to complete the action. This should be driven from a JSON document which documents all the flows.
