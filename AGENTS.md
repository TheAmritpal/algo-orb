# Instructions for LLM Agents

## Project purpose

This project is an algorithmic trading application that will place real, live-money orders through Upstox.

## Required technology stack

- Use Python with FastAPI for the backend and HTTP routes.
- Manage the Python project and dependencies with uv and run the application with Uvicorn.
- Use Jinja2 templates for server-rendered pages.
- Use MySQL as the application database.
- Manage database schema changes with Alembic migrations; never create or alter tables in request handlers.
- Use the official Upstox Python SDK for broker integration and algorithmic trade execution.

## Development instructions

- Keep application code, templates, database access, and broker integration organized into clear modules.
- Keep API credentials and database passwords in environment variables. Store OAuth access tokens in the MySQL `upstox_tokens` table; never commit or log secrets.
- Treat order placement, modification, and cancellation as operations affecting real money.
- Validate order inputs and enforce configured trading limits before sending orders to Upstox.
- Handle broker errors and uncertain order states explicitly; check order status before retrying to avoid duplicate orders.
- Use mocks or sandbox facilities for automated tests. Do not submit live orders while developing or testing unless the user explicitly authorizes the specific live trading activity.
- Record order requests, broker responses, and order status changes for troubleshooting without exposing secrets.
