# Debug Dojo — Deployment Guide

This guide explains how to deploy **Debug Dojo** to the cloud, specifically covering **Vercel** for the frontend and Docker-enabled hosting for the backend sandbox.

---

## 1. Architecture & Platform Suitability

Debug Dojo consists of two decoupled components:

| Component | Stack | Requirements | Ideal Platform |
|---|---|---|---|
| **Frontend** | Static SPA (React, Tailwind, CodeMirror, Shippori Mincho) | Static web hosting, Global CDN | **Vercel**, Netlify, GitHub Pages, Cloudflare Pages |
| **Backend API & Runner** | FastAPI (Python 3.12+), SQLite (WAL mode), Gemini AI (`google-genai`) | **Docker daemon** for container sandboxing (`debugdojo-runner`), persistent disk | **Railway**, **Render**, **Fly.io**, or any Linux VPS (Ubuntu + Docker) |

> [!IMPORTANT]
> **Why Vercel cannot run the Backend Sandbox:**
> Debug Dojo's security model strictly mandates that untrusted learner code never runs directly in the host process — it must execute inside ephemeral, isolated Docker containers (`debugdojo-runner`) with CPU, memory, PID, and network limits. Serverless platforms like **Vercel Functions and AWS Lambda do not provide a Docker daemon** and therefore cannot run Docker containers.

---

## 2. Deploying the Frontend to Vercel

The frontend is ready for immediate deployment on Vercel.

### Step 1: Import Repository to Vercel
1. Log in to [Vercel](https://vercel.com).
2. Click **"Add New..."** -> **"Project"**.
3. Select your GitHub repository: `ParthKulkarni29/debug-dojo-updated`.

### Step 2: Configure Project Settings
- **Framework Preset**: `Other`
- **Root Directory**: `./` (leave default, or set to `frontend`)
- **Build and Output Settings**: Default (no build command needed — the pre-configured `vercel.json` automatically maps routes and assets).
- Click **"Deploy"**.

Your frontend will be live in seconds at `https://debug-dojo-updated.vercel.app` (or your assigned Vercel URL).

---

## 3. Running & Connecting the Backend

### Option A: Interactive Demo Mode (Zero Backend Needed)
If you want to showcase the frontend without spinning up a live backend server:
- Open `https://your-vercel-domain.vercel.app/?mock=1`
- Or click **"Try Demo / Mock Mode"** on the welcome/connection card.
- The built-in client-side mock engine simulates tests, AST mistake taxonomy, Socratic coach feedback, and mastery tracking.

### Option B: Connecting a Live Backend
When your backend is running (locally or on a cloud host):
1. **Via URL Parameter**:
   ```
   https://your-vercel-domain.vercel.app/?api=https://your-backend-domain.com
   ```
   *(This gets automatically saved to `localStorage` for future visits.)*
2. **Via UI Prompt**:
   If the backend is not reached, click **"Set Backend URL"** on screen and paste your backend endpoint.

---

## 4. Deploying the Backend (Docker-Enabled Host)

To run the live grading engine with real sandboxed Docker execution:

### Deploying to Railway / Render / VPS:
1. Ensure Docker is installed on your host.
2. Build the runner image:
   ```bash
   docker build -t debugdojo-runner docker/runner
   ```
3. Set environment variables in `.env`:
   ```bash
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_MODEL=gemini-2.5-flash
   CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
   CORS_ORIGIN_REGEX=^https://.*\.vercel\.app$
   ```
4. Start the FastAPI host server:
   ```bash
   python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
   ```
