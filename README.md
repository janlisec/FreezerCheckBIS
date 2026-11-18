# FreezerCheckBIS

FreezerCheckBIS is a Python/Flask application for recording freezer temperature
checks in openBIS. A user signs in to an openBIS instance, selects a freezer by
scanning its QR code or entering its perm-ID, reviews the readings, and submits
a new control record linked to that freezer.

## Features

- Select Playground or Main at login (Playground is selected by default).
- Authenticate with the user's own openBIS credentials.
- Load freezer perm-IDs from the configured inventory collection.
- Show the freezer name, complete BAM location, and configured temperature
  limits after a freezer is identified.
- Highlight a recorded minimum below the freezer's allowed minimum, or a
  recorded maximum above its allowed maximum.
- Enter a freezer perm-ID manually; QR scanning is available when supported by
  the browser's `BarcodeDetector` API.
- Default the person and date, and prefill temperatures from the latest
  recorded check for the selected freezer when available.
- Validate the input and link each saved check to its parent freezer.

## Requirements

- Windows for the included desktop launcher; Python 3.12 is the tested runtime.
- Network access to the openBIS instances.
- An openBIS account with permission to view the configured freezer and person
  inventories. Saving also requires permission to create control records in
  the configured collection.
- The relevant collections, object types, and property codes must exist in each
  configured instance.

## Local setup

From PowerShell in the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item freezercheckbis.config.example.json freezercheckbis.config.json
```

Edit `freezercheckbis.config.json` with the HTTPS instance URLs, freezer
inventory collections, control collection, object types, and property codes
used by your openBIS environment. This includes the freezer name, complete
location, and min/max temperature property codes. The example contains
placeholder values only.
This local configuration file is ignored by Git.

The app reads the config from `freezercheckbis.config.json` beside the service
module by default. To use another file, set `FREEZERCHECKBIS_CONFIG` to its path.

### Start from the desktop shortcut

Run `start_freezer_check_bis.ps1` or create a Windows shortcut to it. The
launcher uses the project virtual environment, creates a random Flask signing
key under `%LOCALAPPDATA%\FreezerCheckBIS`, starts the app, and opens the login
page in the default browser. It listens on `127.0.0.1:5000` and disables Main
writes. Close the server console window to stop it.

### Start manually

```powershell
$env:FLASK_SECRET_KEY = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
$env:FREEZERCHECKBIS_CONFIG = (Resolve-Path .\freezercheckbis.config.json).Path
$env:FLASK_COOKIE_SECURE = "0"
.\.venv\Scripts\python.exe .\freezer_check_bis_app.py
```

Then open `http://127.0.0.1:5000/login`. `FLASK_COOKIE_SECURE=0` is for local
HTTP development only. Use secure cookies behind HTTPS in any shared
deployment.

## Instance behavior

Playground and Main freezer collection locations are configured independently.
Temperature checks are created in the configured control collection. Freezer
perm-IDs are instance-specific and must not be interchanged.

Playground and Main both allow saves. The signed-in openBIS account must have
permission to create control records in the configured collection. Freezer
perm-IDs are instance-specific and must not be interchanged.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use synthetic config, account, identity, and credential values; they do
not connect to openBIS.

## Deployment notes

The built-in Flask server is for local development and listens only on loopback.
To make the app available to another device, use an IT-approved host reachable
on the employer network, serve it over HTTPS with a certificate trusted by the
device, and run behind a production WSGI server/reverse proxy. The current
session registry is process-local, so use one application worker. Do not expose
the development server directly to the network.

Camera access requires a secure browser context (HTTPS, or localhost for local
development). QR decoding currently depends on browser support for
`BarcodeDetector`; manual perm-ID entry remains available otherwise.
