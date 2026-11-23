import 'dotenv/config';
import { spawn, type ChildProcess } from 'node:child_process';
import express, { type Request, type Response } from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { createServer as createViteServer } from 'vite';

const PYTHON_PORT = Number(process.env.PYTHON_PORT || 8001);
const PYTHON_BASE_URL = `http://127.0.0.1:${PYTHON_PORT}`;

function spawnPythonBackend(): ChildProcess {
  const child = spawn('python3', ['-u', 'backend/main.py', '--host', '127.0.0.1', '--port', String(PYTHON_PORT)], {
    stdio: 'inherit',
    env: process.env,
  });

  child.on('exit', (code, signal) => {
    if (signal !== 'SIGTERM' && signal !== 'SIGINT') {
      console.error(`Python backend exited unexpectedly (code=${code}, signal=${signal})`);
    }
  });

  return child;
}

async function waitForPythonHealth(timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${PYTHON_BASE_URL}/api/health`);
      if (res.ok) return;
    } catch {
      // Wait for Python HTTP server socket to bind
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`Python backend did not respond on ${PYTHON_BASE_URL} within ${timeoutMs}ms`);
}

async function proxyToPython(req: Request, res: Response): Promise<void> {
  try {
    const targetUrl = `${PYTHON_BASE_URL}${req.originalUrl}`;
    const hasBody = req.method !== 'GET' && req.method !== 'HEAD';
    const bodyText = hasBody && req.body !== undefined ? JSON.stringify(req.body) : undefined;

    const upstream = await fetch(targetUrl, {
      method: req.method,
      headers: {
        'Content-Type': 'application/json',
      },
      body: bodyText,
    });

    const retryAfter = upstream.headers.get('retry-after');
    if (retryAfter) {
      res.setHeader('Retry-After', retryAfter);
    }
    const contentType = upstream.headers.get('content-type') || 'application/json; charset=utf-8';
    res.setHeader('Content-Type', contentType);

    const payload = await upstream.text();
    res.status(upstream.status).send(payload);
  } catch (err) {
    res.status(502).json({
      error: err instanceof Error ? err.message : 'Unable to reach Python backend.',
    });
  }
}

async function startGateway() {
  const pyChild = spawnPythonBackend();

  const cleanup = () => {
    if (!pyChild.killed) {
      pyChild.kill('SIGTERM');
    }
  };
  process.on('SIGINT', () => {
    cleanup();
    process.exit(0);
  });
  process.on('SIGTERM', () => {
    cleanup();
    process.exit(0);
  });
  process.on('exit', cleanup);

  await waitForPythonHealth();

  const app = express();
  app.use(express.json({ limit: '1mb' }));

  app.use('/api', (req, res) => {
    void proxyToPython(req, res);
  });

  const isProd = process.env.NODE_ENV === 'production';
  const distDir = path.resolve(process.cwd(), 'dist');

  if (isProd && fs.existsSync(distDir)) {
    app.use(express.static(distDir));
    app.get('*', (_req, res) => {
      res.sendFile(path.join(distDir, 'index.html'));
    });
  } else {
    const vite = await createViteServer({
      server: {
        middlewareMode: true,
        hmr: false,
        ws: false,
      },
      appType: 'spa',
    });

    app.get('/@vite/client', (_req, res) => {
      res.type('application/javascript; charset=utf-8');
      res.send(`
const sheetsMap = new Map();
export function updateStyle(id, content) {
  let style = sheetsMap.get(id);
  if (!style) {
    style = document.createElement('style');
    style.setAttribute('type', 'text/css');
    style.setAttribute('data-vite-dev-id', id);
    document.head.appendChild(style);
    sheetsMap.set(id, style);
  }
  style.textContent = content;
}
export function removeStyle(id) {
  const style = sheetsMap.get(id);
  if (style) {
    document.head.removeChild(style);
    sheetsMap.delete(id);
  }
}
export function injectQuery(url) {
  return url;
}
export function createHotContext() {
  return {
    data: {},
    accept() {},
    acceptExports() {},
    dispose() {},
    prune() {},
    decline() {},
    invalidate() {},
    on() {},
    off() {},
    send() {},
  };
}
export class ErrorOverlay extends HTMLElement {}
`);
    });

    app.use(vite.middlewares);
  }

  const port = isProd ? Number(process.env.PORT || 3000) : 3000;
  app.listen(port, '0.0.0.0', () => {
    console.log(`Déjà Fix gateway listening on http://0.0.0.0:${port} -> Python backend ${PYTHON_BASE_URL}`);
  });
}

startGateway().catch((err) => {
  console.error('Failed to start Déjà Fix:', err);
  process.exit(1);
});
