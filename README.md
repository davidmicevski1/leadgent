# LeadGent Agency OS

LeadGent is an execution-first agency focused on outbound and GTM automations.

This repo now includes a local control center where you can:
- Create and update tasks
- Move tasks across `To Do`, `In Progress`, and `Done` with drag-and-drop
- Delete tasks
- Add notes to existing tasks
- Edit your markdown docs directly in the browser

## Project Structure

- `dashboard/`: Frontend UI for task + docs management
- `scripts/dashboard_server.py`: Local API/static server
- `scripts/start_dashboard.sh`: Shortcut to run the dashboard
- `data/tasks.json`: Persisted task data
- `docs/`: Strategy and launch documents
- `templates/`: Reusable delivery templates

## Run Dashboard

```bash
./scripts/start_dashboard.sh 8080
```

Open:

- `http://127.0.0.1:8080/dashboard/`

Login:

- `David`
- `Viktorija`

Default passwords (change before hosting):

- `ChangeMe-David`
- `ChangeMe-Viktorija`

For hosted use, set environment variables:

```bash
export LEADGENT_DAVID_PASSWORD='your-strong-password'
export LEADGENT_VIKTORIJA_PASSWORD='your-strong-password'
```

## Notes

- Task changes are saved to `data/tasks.json`.
- Doc edits are saved directly to files under `docs/` and `templates/`.
- Stats/API performance sync can be added later on top of this foundation.
