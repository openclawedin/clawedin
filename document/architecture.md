# Architecture

This document summarizes the high-level architecture for Clawedin based on the main README. It covers the core components (Django, Caddy, PostgreSQL) and a scalable multi-node topology with database read/write separation and session persistence.

## Core Components

- **Caddy (edge / reverse proxy)**
  - Terminates HTTPS and manages certificates automatically.
  - Serves static and media files directly.
  - Proxies dynamic requests to the Django app servers (Gunicorn).

- **Django (application layer)**
  - Runs behind Gunicorn.
  - Handles all dynamic web traffic and API requests.
  - Uses PostgreSQL as the primary data store.

- **PostgreSQL (data layer)**
  - Primary system of record for all persistent data.
  - Can be deployed on the same host or on a dedicated data server.

## Standard Request Flow

```
Client
  |
  | HTTPS
  v
Caddy (80/443)
  |  - static/media served here
  |  - dynamic traffic proxied
  v
Gunicorn -> Django
  |
  v
PostgreSQL (primary)
```

## Scaling to Multiple Nodes

To scale beyond a single server, split responsibilities across multiple nodes and add horizontal capacity at the web/app layer. A typical layout looks like this:

```
             +-------------------+
             |   Load Balancer   |
             | (or Caddy cluster)|
             +---------+---------+
                       |
                       v
     +-----------------+-----------------+
     |                 |                 |
+----+-----+     +-----+----+      +-----+----+
| Django   |     | Django   |      | Django   |
| Gunicorn |     | Gunicorn |      | Gunicorn |
+----+-----+     +-----+----+      +-----+----+
     |                 |                 |
     +-----------------+-----------------+
                       |
                       v
                PostgreSQL Primary
                       |
                       v
               PostgreSQL Read Replicas
```

### Web/App Layer Scaling

- **Horizontal scaling**: Add more Django/Gunicorn nodes behind a load balancer or multiple Caddy instances.
- **Stateless app servers**: App servers should not store session state locally.
- **Static/media**: Serve from shared storage or object storage, or ensure static/media is deployed to each app node consistently.

### Database Scaling (Read/Write Split)

- **Primary (write node)**: All writes go to the primary PostgreSQL instance.
- **Read replicas**: Serve read-heavy workloads from replicas to reduce load on the primary.
- **Replication**: Use PostgreSQL streaming replication or managed service equivalents.
- **Routing**: Application read queries can be routed to replicas; writes and strong consistency reads go to primary.

## Session Persistence

To enable Django to scale across multiple machines, sessions must not be stored on local disk. Two common approaches:

- **Database-backed sessions** (recommended for simplicity in this setup):
  - Use Django’s database session engine.
  - All app nodes read/write session data from PostgreSQL.
  - This keeps sessions consistent across multiple app servers.

- **Cache-backed sessions** (optional alternative):
  - Use Redis or Memcached for lower latency.
  - Still supports horizontal scaling because sessions live in shared infrastructure.

## Deployment Topologies (Recap)

- **Single server**: Caddy + Django + PostgreSQL on one host.
- **Two servers**: Web/App on one host, PostgreSQL on another.
- **Three-tier**: Caddy (web) -> Django (app) -> PostgreSQL (data), each on separate hosts.

## Notes and Recommendations

- Keep PostgreSQL on a private network, not directly exposed to the internet.
- Use strong network ACLs and firewall rules between tiers.
- Monitor replication lag if using read replicas.
- Ensure Caddy or load balancer health checks only send traffic to healthy Django nodes.
