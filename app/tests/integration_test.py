# Run test in Docker container with: xvfb-run pytest -s tests/integration_test.py 
import pytest
import asyncio
import socket
from playwright.async_api import async_playwright

# Функция ожидания сервера
async def wait_for_server(host="127.0.0.1", port=8000, timeout=20):
    for _ in range(timeout * 2):
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            await asyncio.sleep(0.5)
    raise RuntimeError(f"Server {host}:{port} не отвечает")

@pytest.mark.asyncio
async def test_user_can_write_in_chat():
    # Ждем пока сервер поднимется
    await wait_for_server("127.0.0.1", 8000)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        await page.goto("http://127.0.0.1:8000")

        await page.fill("#message", "Как подключить домашний интернет?")
        await page.click(".send-btn")

        bot_locator = page.locator("#chat .bubble.bot").last
        await bot_locator.wait_for(state="visible", timeout=20000)

        bot_reply = await bot_locator.inner_text()
        assert bot_reply.strip() != ""
        print("Ответ бота:", bot_reply)
        await browser.close()
