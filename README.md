# Lightweight MySQL InnoDB Cluster Monitor

This is a low-overhead monitor for your 3-node MySQL 8.4 InnoDB Cluster.

Default light settings:
- poll every 120 seconds
- only 2 TCP samples per DB node
- metrics JSONL every 10 minutes
- no SSH OS checks
- no MySQL error-log scanning
- state-change alerts only
- email/Telegram only for WARNING/CRITICAL, PRIMARY change, and RECOVERY

## Files
- `mysql_cluster_monitor_light.py`
- `config.example.json`
- `env.example`
- `create_monitor_user.sql`
- `mysql-cluster-monitor-light.service`
- `mysql-cluster-monitor-light.logrotate`
- `CODE_WALKTHROUGH.txt`

## Recommended host
Best: separate small monitoring VM.
POC: osTicket/MySQL Router host is acceptable.
Do not run the only monitor on DBNODE1/2/3.

## Install
```bash
sudo apt update
sudo apt install -y python3 python3-venv
sudo mkdir -p /opt/mysql-cluster-monitor /etc/mysql-cluster-monitor
sudo mkdir -p /var/log/mysql-cluster-monitor /var/lib/mysql-cluster-monitor
sudo python3 -m venv /opt/mysql-cluster-monitor/venv
sudo /opt/mysql-cluster-monitor/venv/bin/pip install mysql-connector-python
sudo cp mysql_cluster_monitor_light.py /opt/mysql-cluster-monitor/
sudo cp config.example.json /etc/mysql-cluster-monitor/config.json
sudo cp env.example /etc/mysql-cluster-monitor/env
sudo chmod 600 /etc/mysql-cluster-monitor/env
```

## Variables to replace
In `/etc/mysql-cluster-monitor/config.json` replace:
- osTicket URL if application checking will be enabled
- SMTP server/user/from/To/Cc
- Telegram settings if used
- latency thresholds if you later tune them

In `/etc/mysql-cluster-monitor/env` replace:
- `CLMON_DB_PASSWORD`
- `CLMON_SMTP_PASSWORD` if used
- Telegram token/chat ID if used

## Monitoring DB user
Run on current PRIMARY:
```sql
CREATE USER 'cluster_monitor'@'10.22.27.144'
IDENTIFIED BY 'REPLACE_WITH_STRONG_PASSWORD'
REQUIRE SSL;

GRANT SELECT ON performance_schema.*
TO 'cluster_monitor'@'10.22.27.144';
```

## Load secret variables for manual testing
```bash
set -a
. /etc/mysql-cluster-monitor/env
set +a
```

## Validate config
```bash
/opt/mysql-cluster-monitor/venv/bin/python   /opt/mysql-cluster-monitor/mysql_cluster_monitor_light.py   --config /etc/mysql-cluster-monitor/config.json   --validate-config
```
Expected:
```text
CONFIG OK
```

## One test cycle
```bash
/opt/mysql-cluster-monitor/venv/bin/python   /opt/mysql-cluster-monitor/mysql_cluster_monitor_light.py   --config /etc/mysql-cluster-monitor/config.json   --once
```

## Raw JSON output
```bash
/opt/mysql-cluster-monitor/venv/bin/python   /opt/mysql-cluster-monitor/mysql_cluster_monitor_light.py   --config /etc/mysql-cluster-monitor/config.json   --once --print-json
```

## Faster temporary interval for failover test
```bash
/opt/mysql-cluster-monitor/venv/bin/python   /opt/mysql-cluster-monitor/mysql_cluster_monitor_light.py   --config /etc/mysql-cluster-monitor/config.json   --interval 20
```

## Test alerts
```bash
/opt/mysql-cluster-monitor/venv/bin/python   /opt/mysql-cluster-monitor/mysql_cluster_monitor_light.py   --config /etc/mysql-cluster-monitor/config.json   --test-alert
```

## Disable optional parts
```bash
--disable-app-check
--disable-email
--disable-telegram
```

## All flags
```text
--config PATH
--once
--validate-config
--test-alert
--print-json
--interval SECONDS
--disable-app-check
--disable-email
--disable-telegram
```

## systemd
```bash
sudo cp mysql-cluster-monitor-light.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mysql-cluster-monitor-light
sudo systemctl status mysql-cluster-monitor-light --no-pager
```

Resource caps included:
```text
CPUQuota=5%
MemoryMax=150M
```

## Logs
```text
/var/log/mysql-cluster-monitor/monitor.log
/var/log/mysql-cluster-monitor/metrics.jsonl
/var/log/mysql-cluster-monitor/events.jsonl
```

## Default latency flags
```text
OK       <100 ms
CHECK    >=100 ms
WATCH    >=200 ms, partial failure, or high jitter
CRITICAL >=500 ms, major loss, or unreachable
```

These are starter values. Tune after collecting your normal WAN baseline.

## Expected healthy output
```text
Cluster: osticketCluster
PRIMARY: dbnode2.iestinc.internal
dbnode1.iestinc.internal: ONLINE / SECONDARY
dbnode2.iestinc.internal: ONLINE / PRIMARY
dbnode3.iestinc.internal: ONLINE / SECONDARY
Router: UP
DBNODE1: OK
DBNODE2: OK
DBNODE3: OK
Actionable conditions: none
```
