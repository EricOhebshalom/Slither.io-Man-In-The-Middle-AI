# Slither.io Autonomous AI Bot & Network Interceptor 

An autonomous AI bot for [slither.io](http://slither.io), built using Python and `mitmproxy`. This project intercepts live WebSocket and HTTP traffic to expose the game's internal client-side state, allowing an asynchronous Python engine to inject real-time steering and pathfinding commands.

- **Network Protocol Reverse-Engineering:** Utilizes `mitmproxy` (and network analysis via Wireshark) to intercept the game's core network requests.
- **Client-Side Engine Modification:** Intercepts and serves a locally modified `slither_engine.js` file, exposing internal arrays (snake locations, food sizes, enemy hitboxes) without triggering server-side anti-cheat mechanisms.
- **Asynchronous AI Loop:** A high-speed `asyncio` loop continuously evaluates spatial data to make sub-second decisions.
- **Advanced Pathfinding:**
  - **O(N) Density Mapping:** Calculates food density using a local grid system to prioritize high-value targets.
  - **Geometric Evasion:** Uses point-to-segment distance calculations to detect and evade enemy snake hitboxes.
- **Bloodlust Mode:** Dedicated logic to track and pursue large clusters of food dropped by recently deceased snakes.

## Technology Stack

- **Python 3.x**
- **mitmproxy** (Man-in-the-Middle proxy for HTTP/WebSocket interception)
- **asyncio** (Asynchronous I/O for real-time game loops)
- **JavaScript** (Modified client-side game engine)

## Project Structure

- `slither_capture.py`: The core `mitmproxy` addon containing the autonomous AI logic, distance calculations, and grid-mapping engines.
- `slither_engine.js`: The modified local copy of the game's core engine, injected into the browser at runtime.
- `telemetry_injection.patch`: The patch file to be put on the original slither.io source code to create `slither_engine.js`
- `highscore.txt`: Local persistence for tracking the bot's highest achieved score.

## How it Works

1. **Proxy Setup:** The user routes their web browser traffic through the local `mitmproxy` server.
2. **Interception:** When the browser requests the main game engine (`slither.io/s/game*.js`), the proxy intercepts the request and serves our modified `slither_engine.js` instead.
3. **Data Extraction:** The modified JS file seamlessly hooks into the active memory arrays (Hitboxes, Food, Enemy Heads) and exports the data back to the Python backend.
4. **AI Decision Making:** The Python script parses the coordinate data, applies density mapping and geometric distance algorithms, and computes the optimal turning angle.
5. **Command Injection:** The optimal steering angle is pushed back to the client and executed by the browser, driving the snake autonomously.

## Disclaimer

This project is for educational purposes only. It was built as a passion project to explore network interception, asynchronous Python programming, and algorithmic geometric pathfinding. I am not providing a copy of the original slither.io source code or how I found it to create the patch file as that is confidential information. 
