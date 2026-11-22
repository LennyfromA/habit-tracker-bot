"""Automated background Cloudflare tunnel manager for 24/7 server execution without a local PC."""

import asyncio
import logging
import os
import platform
import re
import shutil
import urllib.request
from typing import Optional

from bot.config import settings

logger = logging.getLogger(__name__)

_tunnel_proc: Optional[asyncio.subprocess.Process] = None


def get_cloudflared_path() -> str:
    """Determine binary path for cloudflared."""
    is_win = platform.system() == "Windows"
    binary_name = "cloudflared.exe" if is_win else "cloudflared"

    # Check current working directory
    if os.path.exists(binary_name):
        return os.path.abspath(binary_name)

    # Check system PATH
    system_path = shutil.which("cloudflared")
    if system_path:
        return system_path

    return os.path.abspath(binary_name)


async def ensure_cloudflared_binary() -> Optional[str]:
    """Download cloudflared binary if not found."""
    path = get_cloudflared_path()
    if os.path.exists(path):
        return path

    is_win = platform.system() == "Windows"
    arch = platform.machine().lower()

    if is_win:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    else:
        if "arm" in arch or "aarch64" in arch:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
        else:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

    logger.info("Downloading cloudflared binary from %s to %s ...", url, path)
    try:
        def _download():
            req = urllib.request.Request(url, headers={"User-Agent": "HabitTrackerBot/1.0"})
            with urllib.request.urlopen(req, timeout=45) as resp, open(path, "wb") as f:
                shutil.copyfileobj(resp, f)

        await asyncio.to_thread(_download)
        if not is_win:
            os.chmod(path, 0o755)
        logger.info("cloudflared binary successfully downloaded.")
        return path
    except Exception as e:
        logger.error("Failed to download cloudflared binary: %s", e)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return None


async def start_auto_tunnel(local_port: int, token: Optional[str] = None) -> Optional[str]:
    """Start cloudflared in the background and return public HTTPS URL."""
    global _tunnel_proc

    bin_path = await ensure_cloudflared_binary()
    if not bin_path or not os.path.exists(bin_path):
        logger.warning("cloudflared binary is not available. Auto-tunnel could not be started.")
        return None

    if token:
        cmd = [bin_path, "tunnel", "--protocol", "http2", "run", "--token", token]
    else:
        cmd = [
            bin_path,
            "tunnel",
            "--protocol",
            "http2",
            "--url",
            f"http://127.0.0.1:{local_port}",
            "--no-autoupdate",
        ]

    logger.info("Starting auto-tunnel process: %s", " ".join(cmd))
    try:
        _tunnel_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        logger.error("Failed to spawn cloudflared process: %s", e)
        return None

    if token:
        # Predefined domain configured in Cloudflare Zero Trust
        return settings.WIDGET_BASE_URL or "https://configured-in-cloudflare.com"

    # Quick tunnel: extract https://*.trycloudflare.com from stderr/stdout
    tunnel_url = None
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    start_t = asyncio.get_event_loop().time()
    max_wait = 45.0  # Allow up to 45 seconds for Cloudflare registration

    try:
        while asyncio.get_event_loop().time() - start_t < max_wait:
            if _tunnel_proc.returncode is not None:
                logger.error("cloudflared exited unexpectedly with code %s", _tunnel_proc.returncode)
                break

            time_left = max(1.0, max_wait - (asyncio.get_event_loop().time() - start_t))
            try:
                line_bytes = await asyncio.wait_for(_tunnel_proc.stdout.readline(), timeout=time_left)
            except asyncio.TimeoutError:
                logger.warning("Timed out after %s seconds waiting for cloudflared tunnel URL", max_wait)
                break

            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line:
                logger.info("[cloudflared] %s", line)

            match = url_pattern.search(line)
            if match:
                tunnel_url = match.group(0)
                logger.info("🌐 Auto-tunnel established: %s", tunnel_url)
                break
    except Exception as e:
        logger.warning("Error reading cloudflared tunnel URL: %s", e)

    # Launch background drain task so pipe buffer never fills up
    if _tunnel_proc and _tunnel_proc.returncode is None:
        async def _drain_output(proc: asyncio.subprocess.Process):
            try:
                while proc.returncode is None and proc.stdout:
                    line = await proc.stdout.readline()
                    if not line:
                        break
            except Exception:
                pass
        asyncio.create_task(_drain_output(_tunnel_proc))

    return tunnel_url


async def stop_auto_tunnel() -> None:
    """Terminate the cloudflared background process."""
    global _tunnel_proc
    if _tunnel_proc and _tunnel_proc.returncode is None:
        logger.info("Stopping cloudflared auto-tunnel...")
        try:
            _tunnel_proc.terminate()
            await asyncio.wait_for(_tunnel_proc.wait(), timeout=5.0)
        except Exception:
            try:
                _tunnel_proc.kill()
            except Exception:
                pass
        _tunnel_proc = None
        logger.info("Auto-tunnel stopped.")
