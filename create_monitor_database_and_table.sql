CREATE DATABASE IF NOT EXISTS monitor;

CREATE TABLE IF NOT EXISTS monitor.network_health (
    event_id CHAR(64) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    monitor_name VARCHAR(128) NULL,
    cluster_name VARCHAR(128) NULL,
    target_node VARCHAR(128) NOT NULL,
    target_host VARCHAR(255) NOT NULL,
    tcp_min_ms DECIMAL(10,3) NULL,
    tcp_avg_ms DECIMAL(10,3) NULL,
    tcp_max_ms DECIMAL(10,3) NULL,
    jitter_ms DECIMAL(10,3) NULL,
    packet_loss_pct DECIMAL(6,3) NULL,
    latency_state VARCHAR(20) NULL,
    member_state VARCHAR(30) NULL,
    member_role VARCHAR(30) NULL,
    transactions_in_queue BIGINT UNSIGNED NULL,
    applier_queue BIGINT UNSIGNED NULL,
    conflicts_detected BIGINT UNSIGNED NULL,
    PRIMARY KEY (event_id),
    KEY ix_network_health_observed_at (observed_at),
    KEY ix_network_health_node_time (target_node, observed_at)
) ENGINE=InnoDB;

-- Run the GRANT using the same host definition already used by cluster_monitor.
-- Adjust the host only if your existing account uses a different host value.
GRANT INSERT, SELECT ON monitor.network_health TO 'cluster_monitor'@'10.22.27.144';

