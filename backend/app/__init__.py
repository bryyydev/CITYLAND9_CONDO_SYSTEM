"""CityLand 9 backend package (target architecture, see docs/architecture.md).

During the migration the running application is still backend/legacy_app.py.
New REST blueprints live in app.routes and are registered on that application;
the application factory (create_app) is added once modules move over.
"""
