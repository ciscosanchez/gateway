global:
  resolve_timeout: 5m
  # $ALERTMANAGER_SLACK_WEBHOOK is substituted at container start by
  # entrypoint.sh. If unset, this field is empty and the slack receiver is
  # effectively a no-op (send will fail, alerts still fire to PagerDuty/default).
  slack_api_url: "%%ALERTMANAGER_SLACK_WEBHOOK%%"

route:
  receiver: default
  group_by: [alertname, severity]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - receiver: pagerduty
      matchers: [severity="critical"]
      continue: true
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

inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers:  [severity="warning"]
    equal: [alertname, cluster, service]
