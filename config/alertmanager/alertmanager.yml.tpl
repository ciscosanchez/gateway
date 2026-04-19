global:
  resolve_timeout: 5m
  # $ALERTMANAGER_SLACK_WEBHOOK is substituted at container start by
  # entrypoint.sh. If unset, this field is empty and the slack receiver is
  # effectively a no-op (send will fail, alerts still fire to PagerDuty/default).
  slack_api_url: "%%ALERTMANAGER_SLACK_WEBHOOK%%"

route:
  receiver: default
  # Group by alertname + integration so a burst of related alerts becomes one
  # ticket; waiting 30s + repeat 4h prevents ticket spam while giving ops time
  # to see the first notification before duplicates.
  group_by: [alertname, integration, service]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    # CRITICAL only goes to the helpdesk (Zammad via n8n). Minimum-duration
    # gating is enforced by each rule's `for:` clause in the Prometheus rules
    # file - Alertmanager receives an alert only after it's been firing that
    # long, so flaps never reach a ticket.
    - receiver: zammad
      matchers: [severity="critical"]
      continue: true
    - receiver: pagerduty
      matchers: [severity="critical"]
      continue: true
    # Slack gets warnings AND criticals for real-time visibility; warnings
    # never create tickets.
    - receiver: slack
      matchers: [severity=~"warning|critical"]

receivers:
  - name: default
    # No-op sink. Override via Slack/PagerDuty below.

  - name: slack
    slack_configs:
      - send_resolved: true
        channel: "#gateway-alerts"
        title: "[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}"
        text: |
          {{ range .Alerts }}
          *{{ .Annotations.summary }}*
          {{ .Annotations.description }}
          Labels: {{ range .Labels.SortedPairs }}`{{ .Name }}={{ .Value }}` {{ end }}
          {{ end }}

  - name: pagerduty
    pagerduty_configs:
      - routing_key: "%%ALERTMANAGER_PAGERDUTY_KEY%%"
        send_resolved: true
        description: "{{ .CommonAnnotations.summary }}"

  # Zammad via n8n. The workflow (workflows/alertmanager-to-zammad.json)
  # severity-filters (critical only), dedupes via groupKey->external_id, and
  # appends articles on re-fire instead of creating duplicate tickets. Both
  # false-positive filters (severity + dedup) are in the workflow, not here,
  # so an operator tweaking thresholds doesn't need to touch alertmanager.yml.
  - name: zammad
    webhook_configs:
      - url: "http://n8n:5678/webhook/alertmanager"
        send_resolved: true
        # Alertmanager uses its own retry on webhook failures; if n8n is
        # down the alert will be re-attempted over the next few minutes.
        max_alerts: 0    # 0 = no truncation

inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers:  [severity="warning"]
    equal: [alertname, cluster, service]
