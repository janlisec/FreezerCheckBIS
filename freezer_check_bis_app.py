"""Flask login and server-side session shell for freezer temperature checks."""

import hmac
import math
import os
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from requests.exceptions import RequestException

from freezer_check_bis_service import (
    OpenBISSession,
    authenticate_openbis,
    create_temperature_check,
    get_latest_temperature_check,
)

SESSION_LIFETIME_SECONDS = 4 * 60 * 60

LOGIN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FreezerCheckBIS - openBIS login</title>
  <style>
    body { font-family: sans-serif; margin: 0; padding: 1rem; }
    main { margin: 8vh auto 0; max-width: 32rem; }
    h1, main > p { text-align: center; }
    label { display: block; margin-top: 1rem; }
    input, button { 
      box-sizing: border-box; 
      font: inherit; 
      font-size: 1.15rem; 
      min-height: 3.25rem; 
      padding: .5rem; width: 100%; 
    }
    fieldset { border: 1px solid #999; margin-top: 1rem; padding: .5rem 1rem 1rem; }
    legend { padding: 0 .25rem; }
    .instance-option { align-items: center; display: flex; gap: .6rem; margin-top: .5rem; }
    .instance-option input { height: 1.2rem; margin: 0; min-height: 0; width: 1.2rem; }
    .instance-option label { margin: 0; }
    button { margin-top: 1rem; }
    button:disabled { cursor: wait; opacity: .65; }
    .spinner { 
      animation: spin 1s linear infinite; 
      border: .2rem solid currentColor; 
      border-right-color: transparent; 
      border-radius: 50%; 
      display: inline-block; 
      height: 1rem; 
      margin-right: .5rem; 
      vertical-align: -.15rem; 
      width: 1rem; 
    }
    .spinner[hidden] { display: none !important; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <main>
    <h1>FreezerCheckBIS</h1>
    <p>Select the openBIS instance and sign in.</p>
    {% for category, message in get_flashed_messages(with_categories=true) %}
      <p role="alert" data-category="{{ category }}">{{ message }}</p>
    {% endfor %}
    <form method="post" action="{{ url_for('login') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <fieldset>
        <legend>openBIS instance</legend>
        <div class="instance-option">
          <input id="instance-playground" name="instance_name" type="radio"
                 value="playground" {% if selected_instance == "playground" %}checked{% endif %}>
          <label for="instance-playground">Playground</label>
        </div>
        <div class="instance-option">
          <input id="instance-main" name="instance_name" type="radio"
                 value="main" {% if selected_instance == "main" %}checked{% endif %}>
          <label for="instance-main">Main</label>
        </div>
      </fieldset>
      <label for="login_name">openBIS login (email)</label>
      <input id="login_name" name="login_name" type="text"
             autocomplete="username" required>
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required>
      <button id="login-button" type="submit" aria-busy="false">
        <span id="login-spinner" class="spinner" aria-hidden="true" hidden></span>
        <span id="login-button-label">Sign in</span>
      </button>
    </form>
  </main>
  <script>
    const loginForm = document.querySelector("form");
    const loginButton = document.getElementById("login-button");
    const loginSpinner = document.getElementById("login-spinner");
    const loginButtonLabel = document.getElementById("login-button-label");

    loginForm.addEventListener("submit", () => {
      loginButton.disabled = true;
      loginButton.setAttribute("aria-busy", "true");
      loginSpinner.hidden = false;
      loginButtonLabel.textContent = "Signing in...";
    });
  </script>
</body>
</html>"""

STARTUP_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Freezer temperature check</title>
  <style>
    body { font-family: sans-serif; margin: 0 auto; max-width: 42rem; padding: 1rem; }
    label { display: block; }
    input, select, button { 
      box-sizing: border-box; 
      font: inherit; 
      font-size: 1.1rem; 
      height: 3.25rem; 
      min-width: 0; 
      padding: .5rem; 
      width: 100%; 
    }
    .field-grid, .scan-grid, .action-grid { 
      display: grid; 
      gap: .75rem; 
      grid-template-columns: repeat(2, minmax(0, 1fr)); 
    }
    .scan-grid { align-items: end; }
    .scan-grid > label, .scan-grid > .wide { grid-column: 1 / -1; }
    .field { min-width: 0; }
    .field label { margin: .25rem 0 .4rem; }
    .temperature-out-of-range { background-color: #f4cccc; }
    .action-grid { margin-top: 1rem; }
    .action-grid button { margin: 0; }
    .freezer-details { 
      display: grid; 
      gap: .35rem .75rem; 
      grid-template-columns: 
      max-content minmax(0, 1fr); 
      margin: .5rem 0 1rem; 
    }
    .freezer-details dt { font-weight: bold; }
    .freezer-details dd { margin: 0; overflow-wrap: anywhere; }
    .spinner { 
      animation: spin 1s linear infinite; 
      border: .2rem solid currentColor; 
      border-right-color: transparent; 
      border-radius: 50%; 
      display: inline-block; 
      height: 1rem; 
      margin-right: .5rem; 
      vertical-align: -.15rem; 
      width: 1rem;
    }
    .spinner[hidden] { display: none !important; }
    button:disabled { cursor: wait; opacity: .65; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .logout-form { display: contents; }
    #temperature-fields { margin-top: 1rem; }
    #selected-freezer, #scan-status, #freezer-status, #camera-preview { grid-column: 1 / -1; }
    #selected-freezer { overflow-wrap: anywhere; }
    #scan-status, #freezer-status { margin: 0; min-height: 1.5rem; }
    #camera-preview { background: #222; max-width: 100%; width: 100%; }
    [hidden] { display: none !important; }
    @media (max-width: 34rem) {
      body { padding: .75rem; }
      .field-grid, .scan-grid, .action-grid { gap: .5rem; }
      input, select, button { font-size: 1rem; }
    }
  </style>
</head>
<body>
  <main>
    <h1>Freezer temperature check</h1>
    <p>Signed in to {{ instance_name }} as {{ user_id }}.
       {{ freezers|length }} freezers loaded.</p>
    {% for category, message in get_flashed_messages(with_categories=true) %}
      <p role="alert" data-category="{{ category }}">{{ message }}</p>
    {% endfor %}
    <form id="check-form" method="post" action="{{ url_for('submit_check') }}"
          autocomplete="off">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <input type="hidden" name="submission_token" value="{{ submission_token }}">
      <div class="scan-grid">
        <label for="freezer-perm-id">Scan the freezer QR code or enter its perm-ID</label>
        <input id="freezer-perm-id" name="freezer_perm_id" type="text"
               inputmode="text" autocapitalize="off" spellcheck="false"
               aria-describedby="freezer-status" required>
        <button id="start-scan" type="button">Scan QR code</button>
        <button id="stop-scan" type="button" hidden>Stop scanning</button>
        <video id="camera-preview" playsinline hidden></video>
        <p id="scan-status" class="wide" role="status" aria-live="polite"></p>
        <p id="freezer-status" class="wide" role="status" aria-live="polite"></p>
      </div>
      <section id="temperature-fields" hidden>
        <h2 id="selected-freezer"></h2>
        <p id="selected-freezer-identifier"></p>
        <dl class="freezer-details">
          <dt>Complete BAM Location</dt>
          <dd id="freezer-location"></dd>
          <dt>Temperature Min</dt>
          <dd id="freezer-temp-min"></dd>
          <dt>Temperature Max</dt>
          <dd id="freezer-temp-max"></dd>
        </dl>
        <div class="field-grid">
          <div class="field">
            <label for="temp-min">Minimum temperature (°C)</label>
            <input id="temp-min" name="temp_min_celsius" type="number"
                   step="any" inputmode="decimal" required>
          </div>
          <div class="field">
            <label for="temp-max">Maximum temperature (°C)</label>
            <input id="temp-max" name="temp_max_celsius" type="number"
                   step="any" inputmode="decimal" required>
          </div>
          <div class="field">
            <label for="monitoring-date">Monitoring date</label>
            <input id="monitoring-date" name="monitoring_date" type="date" required>
          </div>
          <div class="field">
            <label for="person-identifier">Person entering the data</label>
            <select id="person-identifier" name="person_identifier" required>
              {% for person in people %}
                <option value="{{ person.identifier }}"
                        {% if person.identifier == default_person_identifier %}selected{% endif %}>
                  {{ person.label }}
                </option>
              {% endfor %}
            </select>
          </div>
        </div>
      </section>
      <div class="action-grid">
        <button id="submit-check" type="submit" disabled aria-busy="false">
          <span id="save-spinner" class="spinner" aria-hidden="true" hidden></span>
          <span id="save-button-label">Save temperature check</span>
        </button>
        <button type="submit" form="logout-form">Sign out</button>
      </div>
    </form>
    <form id="logout-form" class="logout-form" method="post" action="{{ url_for('logout') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    </form>
  </main>
  <script>
    const freezers = {{ freezer_data|tojson }};
    const freezersByPermId = new Map(
      freezers.map((freezer) => [freezer.perm_id, freezer])
    );
    const idInput = document.getElementById("freezer-perm-id");
    const status = document.getElementById("freezer-status");
    const scanStatus = document.getElementById("scan-status");
    const video = document.getElementById("camera-preview");
    const startScan = document.getElementById("start-scan");
    const stopScan = document.getElementById("stop-scan");
    const temperatureFields = document.getElementById("temperature-fields");
    const selectedFreezer = document.getElementById("selected-freezer");
    const selectedFreezerIdentifier = document.getElementById("selected-freezer-identifier");
    const freezerLocation = document.getElementById("freezer-location");
    const freezerTempMin = document.getElementById("freezer-temp-min");
    const freezerTempMax = document.getElementById("freezer-temp-max");
    const monitoringDate = document.getElementById("monitoring-date");
    const tempMin = document.getElementById("temp-min");
    const tempMax = document.getElementById("temp-max");
    const checkForm = document.getElementById("check-form");
    const submitCheck = document.getElementById("submit-check");
    const saveSpinner = document.getElementById("save-spinner");
    const saveButtonLabel = document.getElementById("save-button-label");
    let cameraStream = null;
    let scanFrame = null;
    let detector = null;
    let historyRequestId = 0;
    let selectedPermId = null;
    let selectedFreezerData = null;

    function updateTemperatureWarnings() {
      const minimum = Number(tempMin.value);
      const maximum = Number(tempMax.value);
      const allowedMinimum = Number(selectedFreezerData?.temp_min_celsius);
      const allowedMaximum = Number(selectedFreezerData?.temp_max_celsius);

      tempMin.classList.toggle(
        "temperature-out-of-range",
        Boolean(tempMin.value.trim()) &&
          Number.isFinite(minimum) &&
          Number.isFinite(allowedMinimum) &&
          minimum < allowedMinimum
      );
      tempMax.classList.toggle(
        "temperature-out-of-range",
        Boolean(tempMax.value.trim()) &&
          Number.isFinite(maximum) &&
          Number.isFinite(allowedMaximum) &&
          maximum > allowedMaximum
      );
    }

    function validateFreezerId() {
      const submittedId = idInput.value.trim();
      const freezer = freezersByPermId.get(submittedId);
      if (!freezer) {
        historyRequestId += 1;
        selectedPermId = null;
        selectedFreezerData = null;
        status.textContent = submittedId
          ? "Invalid freezer perm-ID. Check the QR code or enter a valid ID."
          : "";
        temperatureFields.hidden = true;
        selectedFreezer.textContent = "";
        selectedFreezerIdentifier.textContent = "";
        freezerLocation.textContent = "";
        freezerTempMin.textContent = "";
        freezerTempMax.textContent = "";
        updateTemperatureWarnings();
        submitCheck.disabled = true;
        return;
      }

      if (selectedPermId !== freezer.perm_id) {
        tempMin.value = "";
        tempMax.value = "";
        selectedPermId = freezer.perm_id;
        selectedFreezerData = freezer;
        loadLatestReadings(freezer);
      }
      selectedFreezer.textContent = freezer.name || freezer.identifier;
      selectedFreezerIdentifier.textContent = `Identifier: ${freezer.identifier}`;
      freezerLocation.textContent =
        freezer.bam_location_complete || "Not recorded";
      freezerTempMin.textContent = freezer.temp_min_celsius
        ? `${freezer.temp_min_celsius} °C`
        : "Not recorded";
      freezerTempMax.textContent = freezer.temp_max_celsius
        ? `${freezer.temp_max_celsius} °C`
        : "Not recorded";
      updateTemperatureWarnings();
      temperatureFields.hidden = false;
      submitCheck.disabled = false;
    }

    async function loadLatestReadings(freezer) {
      const requestId = ++historyRequestId;
      status.textContent = "Freezer verified. Loading previous readings...";
      try {
        const response = await fetch(
          `/freezers/${encodeURIComponent(freezer.perm_id)}/latest-check`,
          { headers: { "Accept": "application/json" } }
        );
        const result = await response.json();
        if (requestId !== historyRequestId || idInput.value.trim() !== freezer.perm_id) {
          return;
        }
        if (!response.ok) {
          status.textContent = result.error || "Could not load previous readings.";
          return;
        }
        if (result.latest_check) {
          tempMin.value = result.latest_check.temp_min_celsius;
          tempMax.value = result.latest_check.temp_max_celsius;
          updateTemperatureWarnings();
          status.textContent =
            `Freezer verified. Readings loaded from ${result.latest_check.monitoring_date}.`;
        } else {
          status.textContent = "Freezer verified. No previous readings found.";
        }
      } catch (error) {
        if (requestId === historyRequestId) {
          status.textContent = "Could not load previous readings. Enter values manually.";
        }
      }
    }

    function stopCamera() {
      if (scanFrame !== null) {
        cancelAnimationFrame(scanFrame);
        scanFrame = null;
      }
      if (cameraStream !== null) {
        cameraStream.getTracks().forEach((track) => track.stop());
        cameraStream = null;
      }
      video.srcObject = null;
      video.hidden = true;
      stopScan.hidden = true;
      startScan.hidden = false;
    }

    async function scanFrameForQr() {
      if (!cameraStream || !detector) {
        return;
      }
      try {
        const codes = await detector.detect(video);
        if (codes.length > 0) {
          idInput.value = codes[0].rawValue.trim();
          validateFreezerId();
          scanStatus.textContent = "QR code scanned.";
          stopCamera();
          return;
        }
      } catch (error) {
        scanStatus.textContent = "Could not read QR code. Try manual entry.";
        stopCamera();
        return;
      }
      scanFrame = requestAnimationFrame(scanFrameForQr);
    }

    async function startCamera() {
      if (!("BarcodeDetector" in window)) {
        scanStatus.textContent =
          "QR scanning is not supported by this browser. Enter the perm-ID manually.";
        return;
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        scanStatus.textContent =
          "Camera access is unavailable. Enter the perm-ID manually.";
        return;
      }
      try {
        detector = new BarcodeDetector({ formats: ["qr_code"] });
        cameraStream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false
        });
        video.srcObject = cameraStream;
        video.hidden = false;
        await video.play();
        startScan.hidden = true;
        stopScan.hidden = false;
        scanStatus.textContent = "Point the camera at a freezer QR code.";
        scanFrame = requestAnimationFrame(scanFrameForQr);
      } catch (error) {
        stopCamera();
        scanStatus.textContent =
          "Camera unavailable or permission denied. Enter the perm-ID manually.";
      }
    }

    idInput.addEventListener("input", validateFreezerId);
    tempMin.addEventListener("input", updateTemperatureWarnings);
    tempMax.addEventListener("input", updateTemperatureWarnings);
    checkForm.addEventListener("submit", (event) => {
      validateFreezerId();
      if (temperatureFields.hidden || !checkForm.reportValidity()) {
        event.preventDefault();
        if (temperatureFields.hidden) {
          idInput.focus();
        }
        return;
      }
      submitCheck.disabled = true;
      submitCheck.setAttribute("aria-busy", "true");
      saveSpinner.hidden = false;
      saveButtonLabel.textContent = "Saving...";
    });
    startScan.addEventListener("click", startCamera);
    stopScan.addEventListener("click", () => {
      stopCamera();
      scanStatus.textContent = "Scanning stopped.";
    });
    window.addEventListener("pagehide", stopCamera);
    const now = new Date();
    const localDate = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0")
    ].join("-");
    monitoringDate.value = localDate;
  </script>
</body>
</html>"""


@dataclass
class _ServerSession:
    openbis_session: OpenBISSession
    expires_at: float
    submission_token: str | None = None


class _SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _ServerSession] = {}
        self._lock = RLock()

    def add(self, openbis_session: OpenBISSession) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            expired = [key for key, entry in self._sessions.items() if entry.expires_at <= now]
            for key in expired:
                del self._sessions[key]
            self._sessions[session_id] = _ServerSession(
                openbis_session=openbis_session,
                expires_at=now + SESSION_LIFETIME_SECONDS,
            )
        return session_id

    def get(self, session_id: str) -> OpenBISSession | None:
        now = time.monotonic()
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._sessions[session_id]
                return None
            entry.expires_at = now + SESSION_LIFETIME_SECONDS
            return entry.openbis_session

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def issue_submission_token(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.expires_at <= time.monotonic():
                self._sessions.pop(session_id, None)
                return None
            token = secrets.token_urlsafe(32)
            entry.submission_token = token
            return token

    def consume_submission_token(self, session_id: str, submitted: str) -> bool:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.expires_at <= time.monotonic():
                self._sessions.pop(session_id, None)
                return False
            expected = entry.submission_token
            entry.submission_token = None
            return isinstance(expected, str) and hmac.compare_digest(expected, submitted)


def _configured_secret(secret_key: str | None) -> str:
    key = secret_key or os.environ.get("FLASK_SECRET_KEY", "")
    if len(key.encode("utf-8")) < 32:
        raise RuntimeError("Set FLASK_SECRET_KEY to a random secret of at least 32 bytes.")
    return key


def create_app(secret_key: str | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = _configured_secret(secret_key)
    cookie_secure_setting = os.environ.get("FLASK_COOKIE_SECURE", "1").strip()
    if cookie_secure_setting not in {"0", "1"}:
        raise RuntimeError("FLASK_COOKIE_SECURE must be set to 0 or 1.")
    app.config.update(
        MAX_CONTENT_LENGTH=16 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_LIFETIME_SECONDS),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cookie_secure_setting == "1",
    )
    sessions = _SessionStore()

    def ensure_csrf_token() -> str:
        token = session.get("csrf_token")
        if not isinstance(token, str):
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    def validate_csrf_token() -> None:
        expected = session.get("csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not isinstance(expected, str) or not hmac.compare_digest(expected, submitted):
            abort(400, description="Invalid or missing CSRF token.")

    def authenticated_openbis() -> tuple[str, OpenBISSession] | None:
        session_id = session.get("openbis_session_id")
        if not isinstance(session_id, str):
            return None
        openbis_session = sessions.get(session_id)
        if openbis_session is None:
            return None
        return session_id, openbis_session

    def render_home(
        session_id: str,
        openbis_session: OpenBISSession,
        status_code: int = 200,
    ):
        submission_token = sessions.issue_submission_token(session_id)
        if submission_token is None:
            session.clear()
            flash("Your openBIS session expired. Please sign in again.", "error")
            return redirect(url_for("login_form"))
        return (
            render_template_string(
                STARTUP_TEMPLATE,
                user_id=openbis_session.startup.user_id,
                instance_name=openbis_session.instance_name,
                freezers=openbis_session.startup.freezers,
                freezer_data=[
                    {
                        "perm_id": freezer.perm_id,
                        "identifier": freezer.identifier,
                        "name": freezer.name,
                        "bam_location_complete": freezer.bam_location_complete,
                        "temp_min_celsius": freezer.temp_min_celsius,
                        "temp_max_celsius": freezer.temp_max_celsius,
                    }
                    for freezer in openbis_session.startup.freezers
                ],
                people=openbis_session.startup.people,
                default_person_identifier=openbis_session.startup.person_identifier,
                csrf_token=ensure_csrf_token(),
                submission_token=submission_token,
            ),
            status_code,
        )

    @app.get("/login")
    def login_form():
        return render_template_string(
            LOGIN_TEMPLATE,
            csrf_token=ensure_csrf_token(),
            selected_instance="playground",
        )

    @app.post("/login")
    def login():
        validate_csrf_token()
        login_name = request.form.get("login_name", "").strip()
        password = request.form.get("password", "")
        selected_instance = request.form.get("instance_name", "playground")
        if selected_instance not in {"playground", "main"}:
            abort(400, description="Select either playground or main.")
        try:
            openbis_session = authenticate_openbis(
                login_name,
                password,
                instance_name=selected_instance,
            )
        except (ValueError, RuntimeError, RequestException) as error:
            app.logger.warning(
                "FreezerCheckBIS login/startup failed (%s): %s",
                type(error).__name__,
                error,
            )
            flash(str(error), "error")
            return (
                render_template_string(
                    LOGIN_TEMPLATE,
                    csrf_token=ensure_csrf_token(),
                    selected_instance=selected_instance,
                ),
                401,
            )

        session.clear()
        session.permanent = True
        session["csrf_token"] = secrets.token_urlsafe(32)
        session["openbis_session_id"] = sessions.add(openbis_session)
        return redirect(url_for("home"))

    @app.get("/")
    def home():
        authenticated = authenticated_openbis()
        if authenticated is None:
            session.clear()
            flash("Your openBIS session expired. Please sign in again.", "error")
            return redirect(url_for("login_form"))
        session_id, openbis_session = authenticated
        return render_home(session_id, openbis_session)

    @app.get("/freezers/<freezer_perm_id>/latest-check")
    def latest_check(freezer_perm_id: str):
        authenticated = authenticated_openbis()
        if authenticated is None:
            return jsonify(error="Your openBIS session expired. Please sign in again."), 401
        _, openbis_session = authenticated
        try:
            latest = get_latest_temperature_check(openbis_session, freezer_perm_id)
        except ValueError as error:
            app.logger.warning(
                "Invalid latest-check request (%s): %s",
                type(error).__name__,
                error,
            )
            return jsonify(error="Freezer not found."), 404
        except (RuntimeError, RequestException) as error:
            app.logger.warning(
                "Previous temperature lookup failed (%s): %s",
                type(error).__name__,
                error,
            )
            return jsonify(error="Could not load previous temperature readings."), 503

        return jsonify(
            latest_check=(
                {
                    "monitoring_date": latest.monitoring_date,
                    "temp_min_celsius": latest.temp_min_celsius,
                    "temp_max_celsius": latest.temp_max_celsius,
                }
                if latest is not None
                else None
            )
        )

    @app.post("/checks")
    def submit_check():
        validate_csrf_token()
        authenticated = authenticated_openbis()
        if authenticated is None:
            session.clear()
            flash("Your openBIS session expired. Please sign in again.", "error")
            return redirect(url_for("login_form"))
        session_id, openbis_session = authenticated
        freezer_perm_id = request.form.get("freezer_perm_id", "").strip()
        monitoring_date = request.form.get("monitoring_date", "").strip()
        person_identifier = request.form.get("person_identifier", "").strip()
        try:
            temp_min = float(request.form.get("temp_min_celsius", ""))
            temp_max = float(request.form.get("temp_max_celsius", ""))
        except ValueError:
            flash("Enter valid numeric values for both temperatures.", "error")
            return render_home(session_id, openbis_session, 400)

        if not math.isfinite(temp_min) or not math.isfinite(temp_max):
            flash("Temperatures must be finite numbers.", "error")
            return render_home(session_id, openbis_session, 400)

        submitted_token = request.form.get("submission_token", "")
        if not sessions.consume_submission_token(session_id, submitted_token):
            flash(
                "This form was already submitted or has expired. Review and retry.",
                "error",
            )
            return render_home(session_id, openbis_session, 409)

        try:
            record = create_temperature_check(
                openbis_session,
                freezer_perm_id=freezer_perm_id,
                monitoring_date=monitoring_date,
                temp_min_celsius=temp_min,
                temp_max_celsius=temp_max,
                person_identifier=person_identifier,
            )
        except (ValueError, RuntimeError, RequestException) as error:
            app.logger.warning(
                "Temperature check save failed (%s): %s",
                type(error).__name__,
                error,
            )
            flash(f"Temperature check was not saved: {error}", "error")
            return render_home(session_id, openbis_session, 400)

        flash(f"Temperature check saved as {record.identifier}.", "success")
        return redirect(url_for("home"))

    @app.post("/logout")
    def logout():
        validate_csrf_token()
        session_id = session.get("openbis_session_id")
        if isinstance(session_id, str):
            sessions.remove(session_id)
        session.clear()
        return redirect(url_for("login_form"))

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
