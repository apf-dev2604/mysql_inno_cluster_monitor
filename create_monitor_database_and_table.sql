CREATE TABLE monitor.network_health (
    event_id CHAR(36) NOT NULL,

    observed_at DATETIME(6) NOT NULL,
    target_node VARCHAR(100) NOT NULL,

    tcp_min_ms DECIMAL(10,3),
    tcp_avg_ms DECIMAL(10,3),
    tcp_max_ms DECIMAL(10,3),
    jitter_ms DECIMAL(10,3),
    packet_loss_pct DECIMAL(6,3),

    member_state VARCHAR(30),
    member_role VARCHAR(30),

    transactions_in_queue BIGINT,
    applier_queue BIGINT,

    PRIMARY KEY (event_id),
    KEY ix_node_time (target_node, observed_at)
);
