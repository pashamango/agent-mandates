use fleetd::config::Config;
use fleetd::db::Database;
use fleetd::server::{create_router, AppState};
use fleetd::vacuum;
use fleetd::worker;
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "fleetd=info,tower_http=info".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    info!("Starting Mindrock Fleet Dispatcher (fleetd v0.2.0)");

    let config = Config::load();
    info!("Configuration loaded. Listening on {}", config.listen_addr);
    info!("Database path: {:?}", config.db_path);
    info!("Node name: {}", config.node_name);
    info!("agy binary: {:?}", config.agy_bin);
    info!("codex binary: {:?}", config.codex_bin);
    info!("Vacuum enabled: {}, interval: {}s", config.vacuum_enabled, config.vacuum_interval_secs);

    let db = Database::new(&config.db_path)?;
    let recovered = db.reconcile_interrupted_jobs()?;
    if recovered > 0 {
        info!("Reconciled {} interrupted jobs on startup", recovered);
    }

    // Spawn agy worker loop (WIP=1)
    let agy_config = config.clone();
    let agy_db = db.clone();
    tokio::spawn(async move {
        worker::run_builder_loop("agy", agy_config, agy_db).await;
    });

    // Spawn codex worker loop (WIP=1)
    let codex_config = config.clone();
    let codex_db = db.clone();
    tokio::spawn(async move {
        worker::run_builder_loop("codex", codex_config, codex_db).await;
    });

    // Spawn outbox delivery loop
    let outbox_config = config.clone();
    let outbox_db = db.clone();
    tokio::spawn(async move {
        worker::run_outbox_loop(outbox_config, outbox_db).await;
    });

    // Shared vNext SmartRouter, LeaseManager, Ledger, and LinearClient
    let lease_manager = std::sync::Arc::new(fleetd::lease::DurableLeaseManager::new("fleetd-evolution/data/active_leases.json"));
    let ledger = std::sync::Arc::new(fleetd::ledger::RoutingLedger::new("fleetd-evolution/data/routing_ledger.jsonl"));
    let router = std::sync::Arc::new(fleetd::router::SmartRouter::new(300, lease_manager, ledger, false));
    let linear = std::sync::Arc::new(fleetd::linear::LinearClient::new(&config.linear_api_url, &config.linear_api_key));

    // Spawn proactive backlog vacuum loop
    let vacuum_config = config.clone();
    let vacuum_db = db.clone();
    let vacuum_router = router.clone();
    let vacuum_linear = linear.clone();
    tokio::spawn(async move {
        vacuum::run_vacuum_loop(vacuum_config, vacuum_db, vacuum_router, vacuum_linear).await;
    });

    // Start HTTP server
    let app_state = AppState {
        config: config.clone(),
        db: db.clone(),
        router: router.clone(),
        linear: Some(linear.clone()),
    };
    let app = create_router(app_state);

    let listener = tokio::net::TcpListener::bind(config.listen_addr).await?;
    info!("fleetd HTTP server is ready at http://{}", config.listen_addr);

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    info!("fleetd stopped cleanly.");
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install signal handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
