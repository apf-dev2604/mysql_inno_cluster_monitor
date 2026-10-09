CREATE USER 'cluster_monitor'@'10.22.27.144'
IDENTIFIED BY 'REPLACE_WITH_STRONG_PASSWORD'
REQUIRE SSL;

GRANT SELECT ON performance_schema.*
TO 'cluster_monitor'@'10.22.27.144';

SHOW GRANTS FOR 'cluster_monitor'@'10.22.27.144';
