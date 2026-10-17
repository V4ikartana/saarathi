# Brief: health endpoint and status widget

Add operational visibility to the example service.

1. Backend: add `GET /health` returning HTTP 200 with JSON `{"status": "ok"}`, registered in the
   existing route table in `app/server.py`. Add unit tests in `tests/test_health.py`.
2. Frontend: add a small status widget in `web/status-widget.js` that exposes a pure function
   `renderStatus(status)` returning an HTML string containing the status text, and include the
   script in `web/index.html`. Add a Python unit test in `tests/test_widget_files.py` that checks the
   widget file exists, defines `renderStatus`, and that `index.html` references it.

Constraints: standard library only, no new dependencies, no network access. Keep both changes
independent so they can be built in parallel.
