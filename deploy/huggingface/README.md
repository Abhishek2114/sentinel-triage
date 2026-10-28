---
title: Sentinel Triage
emoji: 🛰️
colorFrom: red
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Log anomaly detection and incident triage, live
---

# Sentinel Triage

Detects anomalies in service logs, groups them into incidents, and writes an
evidence-backed triage report with the matching runbook. New incidents reach
the dashboard live over a WebSocket.

This is a **public demo**. Press **Run demo** to generate an hour of
synthetic logs with one injected fault and watch detection and triage run on
them. Every `demo-` service is synthetic data. Writing anything else needs an
API key.

Source, evaluation and design notes:
[github.com/Abhishek2114/sentinel-triage](https://github.com/Abhishek2114/sentinel-triage)
