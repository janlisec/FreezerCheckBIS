# Security

## Overview

FreezerCheckBIS is a local Flask application for recording freezer temperature
checks in openBIS.

The application does not maintain its own user database. Authentication is
performed against openBIS using the user's own account.

## Authentication

Users authenticate with their individual openBIS credentials.

FreezerCheckBIS does not store user passwords on disk.

Authentication is performed through pyBIS against the configured openBIS
instance.

## Credential Handling

Passwords are submitted only during authentication.

The application does not:

- store passwords locally
- write passwords to configuration files
- write passwords to log files
- embed credentials in URLs

The pyBIS login is performed with token persistence disabled.

## Session Handling

The browser receives only a randomly generated session identifier.

The server maintains the corresponding openBIS connection in server-side memory.

The browser cookie does not contain:

- user passwords
- openBIS session objects
- openBIS access tokens

Session identifiers are regenerated during login to prevent session fixation.

## Transport Security

Only HTTPS openBIS endpoints are accepted.

Camera access for QR code scanning relies on browser security requirements and
therefore requires HTTPS or localhost operation.

## Cross-Site Request Forgery Protection

All state-changing requests are protected by CSRF tokens.

CSRF validation is enforced server-side before data is written.

## Input Validation

Submitted freezer records are validated on the server.

Validation includes:

- freezer existence checks
- permitted personnel checks
- numeric temperature validation
- mandatory field checks

Client-side validation is considered a usability feature and is not relied upon
for security.

## Data Storage

The application stores no laboratory records locally.

All freezer control records are written to openBIS.

Session data is maintained in process memory and is removed when the application
terminates.

## Logging

The application records operational events and errors.

Credentials are excluded from logs.

## Reporting Security Issues

Security issues should be reported privately to the maintainer.

Please avoid creating public GitHub issues for unpatched security vulnerabilities.

## Intended Deployment

FreezerCheckBIS is intended for internal use within a trusted organizational
network.

The Flask development server is not intended for production deployment.

For shared deployments the application should be operated behind an approved
reverse proxy and HTTPS endpoint.

## Auditability

FreezerCheckBIS does not maintain a separate business audit log.

All temperature check records are stored in openBIS and remain attributable
to the authenticated openBIS user account used to create them.

No application logging is implemented.
