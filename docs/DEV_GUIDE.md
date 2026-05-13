# Inkwell Dev Guide

## Dev Server

```bash
cd ~/dev/projects/inkwell
python3 -m http.server 8080
```

## Docs

| Page | URL |
|------|-----|
| Workflow Diagram | http://192.168.0.21:8080/docs/workflows.html |
| Architecture Diagram | http://192.168.0.21:8080/docs/architecture.html |

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```
