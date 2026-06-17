/* ─── Last Man Standing — Telegram Mini App JS Engine ─── */

const SERVER_API_URL = window.location.origin;
const SERVER_WS_URL = window.location.origin.replace(/^http/, 'ws') + '/ws';

// ─── Game Constants ───
const WORLD_WIDTH = 4000;
const WORLD_HEIGHT = 4000;
const PLAYER_RADIUS = 12;
const BOSS_RADIUS = 40;
const CAMERA_SPEED = 8;
const PLAYER_SPEED = 160;

// Color schemes
const COLORS = {
    BG: '#0a0a0a',
    WHITE: '#f0f0f0',
    GRAY: '#888888',
    DARK_GRAY: '#333333',
    RED: '#cc0000',
    GREEN: '#00cc44',
    YELLOW: '#ccaa00',
    ORANGE: '#cc5500',
    PANEL_BG: 'rgba(18,18,18,0.85)',
    PANEL_BORDER: 'rgba(55,55,55,0.5)'
};

// ─── Application State ───
let authToken = localStorage.getItem('lms_token') || null;
let username = localStorage.getItem('lms_username') || null;
let userProfile = null;
let tierStatsInterval = null;
let activeTier = null;

// WebSocket Client
let wsConn = null;
let isConnected = false;

// Gameplay Coordinates
let selfPlayerId = null;
let selfX = WORLD_WIDTH / 2;
let selfY = WORLD_HEIGHT / 2;
let playersMap = new Map(); // id -> { username, x, y, is_alive }
let bossState = { state: 'sleeping', x: 0, y: 0, targetX: 0, targetY: 0, time_remaining: 0, target_username: '' };
let hazardZones = [];

// Physics / Viewport
let camX = selfX;
let camY = selfY;
let keysPressed = {};
let particles = []; // Array of particle dicts
let obstacleList = []; // deterministic columns

// Touch Joystick State
let joystickActive = false;
let joystickStartPos = { x: 0, y: 0 };
let joystickOffset = { x: 0, y: 0 }; // normalise range -1 to 1

// Timing
let lastFrameTime = performance.now();
let lastNetworkSend = 0;
const netSendRate = 1000 / 30; // 30Hz throttled movement send

// ─── DOM Elements ───
const screenAuth = document.getElementById('screen-auth');
const screenLobby = document.getElementById('screen-lobby');
const screenGame = document.getElementById('screen-game');

const authTitle = document.getElementById('auth-title');
const emailGroup = document.getElementById('email-group');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');
const emailInput = document.getElementById('email');

const btnAuthSubmit = document.getElementById('btn-auth-submit');
const btnToggleAuth = document.getElementById('btn-toggle-auth');
const btnDeposit = document.getElementById('btn-deposit');
const btnLogout = document.getElementById('btn-logout');
const btnExitGame = document.getElementById('btn-exit-game');

const balanceValue = document.getElementById('balance-value');
const premiumValue = document.getElementById('premium-value');
const ticketsValue = document.getElementById('tickets-value');
const shopProducts = document.getElementById('shop-products');
const toastAlert = document.getElementById('status-toast');

const deathOverlay = document.getElementById('death-overlay');
const deathReason = document.getElementById('death-reason');
const btnDeathExit = document.getElementById('btn-death-exit');

const victoryOverlay = document.getElementById('victory-overlay');
const victoryPrize = document.getElementById('victory-prize');
const btnVictoryExit = document.getElementById('btn-victory-exit');

const bossFlashScreen = document.getElementById('boss-flash-screen');

// Game Panels
const canvas = document.getElementById('game-canvas');
const ctx = canvas.getContext('2d');

const hudTier = document.getElementById('hud-tier');
const hudPrize = document.getElementById('hud-prize');
const hudAlive = document.getElementById('hud-alive');
const hudMovesLabel = document.getElementById('hud-moves-label');
const dot1 = document.getElementById('dot-1');
const dot2 = document.getElementById('dot-2');

const chatInput = document.getElementById('chat-input');
const chatMessages = document.getElementById('chat-messages');
const chatForm = document.getElementById('chat-form');

const joystickContainer = document.getElementById('joystick-container');
const joystickHandle = document.getElementById('joystick-handle');

// ─── INIT & AUTH ROUTINES ───
window.addEventListener('DOMContentLoaded', async () => {
    // Generate deterministic obstacles based on client
    generateObstacles();

    // Check device touch capability to toggle Virtual Joystick
    if ('ontouchstart' in window || navigator.maxTouchPoints > 0) {
        joystickContainer.style.display = 'block';
        setupJoystickEvents();
    }

    // Try automatic Telegram Mini App authentication first
    const isTgAuthed = await tryTelegramAuth();
    if (isTgAuthed) {
        return;
    }

    if (authToken && username) {
        showLobbyScreen();
    } else {
        showAuthScreen();
    }
});

async function tryTelegramAuth() {
    const tg = window.Telegram?.WebApp;
    if (tg && tg.initData) {
        tg.ready();
        tg.expand();

        try {
            const resp = await fetch(`${SERVER_API_URL}/telegram-auth`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ init_data: tg.initData })
            });

            if (resp.ok) {
                const data = await resp.json();
                authToken = data.access_token;

                // Fetch profile to sync username
                const profResp = await fetch(`${SERVER_API_URL}/profile`, {
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (profResp.ok) {
                    const profile = await profResp.json();
                    username = profile.username;
                    localStorage.setItem('lms_token', authToken);
                    localStorage.setItem('lms_username', username);
                    showLobbyScreen();
                    return true;
                }
            }
        } catch (e) {
            console.error('Error during Telegram auth:', e);
        }
    }
    return false;
}

function showAuthScreen() {
    screenAuth.classList.remove('hidden');
    screenLobby.classList.add('hidden');
    screenGame.classList.add('hidden');
}

btnAuthSubmit.addEventListener('click', async () => {
    const u = usernameInput.value.trim();

    if (!u) {
        showToast('Please enter a username', 'error');
        return;
    }

    // Client-side validation: only letters, numbers, underscores, dashes
    if (!/^[a-zA-Z0-9_-]{2,20}$/.test(u)) {
        showToast('Username: 2-20 chars, only letters/numbers/_/-', 'error');
        return;
    }

    try {
        const resp = await fetch(`${SERVER_API_URL}/telegram-auth`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u })
        });

        const data = await resp.json();
        if (!resp.ok) {
            showToast(data.detail || 'Authentication failed', 'error');
            return;
        }

        authToken = data.access_token;
        username = u;
        localStorage.setItem('lms_token', authToken);
        localStorage.setItem('lms_username', username);

        showLobbyScreen();
        showToast('Successfully authenticated!', 'success');
    } catch (e) {
        showToast('API Connection failed', 'error');
    }
});

btnLogout.addEventListener('click', () => {
    authToken = null;
    username = null;
    localStorage.removeItem('lms_token');
    localStorage.removeItem('lms_username');
    clearInterval(tierStatsInterval);
    showAuthScreen();
});


// ─── LOBBY MANAGEMENT ───
async function showLobbyScreen() {
    screenAuth.classList.add('hidden');
    screenLobby.classList.remove('hidden');
    screenGame.classList.add('hidden');

    await fetchLobbyData();
    clearInterval(tierStatsInterval);
    tierStatsInterval = setInterval(fetchLobbyData, 4000);

    // Click cards to join
    document.querySelectorAll('.tier-card').forEach(card => {
        card.onclick = () => {
            const tier = parseInt(card.getAttribute('data-tier'));
            joinTierTournament(tier);
        };
    });
}

async function fetchLobbyData() {
    if (!authToken) return;

    try {
        // Fetch Profile details
        const profResp = await fetch(`${SERVER_API_URL}/profile`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        if (profResp.status === 401) {
            btnLogout.click();
            return;
        }
        if (profResp.ok) {
            userProfile = await profResp.json();
            balanceValue.textContent = `${userProfile.balance.toFixed(2)} CR`;
            ticketsValue.textContent = `${userProfile.chaos_tickets || 0} CHAOS`;
            premiumValue.textContent = userProfile.is_premium ? 'PREMIUM' : 'STANDARD';
            premiumValue.className = userProfile.is_premium ? 'premium-pill active' : 'premium-pill';
            await fetchShopProducts();
        }

        // Fetch Tier Stats
        for (let t = 1; t <= 3; t++) {
            const statsResp = await fetch(`${SERVER_API_URL}/tier-stats/${t}`);
            if (statsResp.ok) {
                const stats = await statsResp.json();
                document.getElementById(`t${t}-prize`).textContent = `Prize: ${stats.prize_pool.toFixed(2)} CR`;
                document.getElementById(`t${t}-alive`).textContent = `Survivors: ${stats.alive} / ${stats.total}`;
                
                const bState = stats.boss_status ? stats.boss_status.state : 'sleeping';
                const bEl = document.getElementById(`t${t}-boss`);
                bEl.textContent = `Boss: ${bState.toUpperCase()}`;
                bEl.className = bState === 'sleeping' ? 'boss-status' : 'boss-status text-red';
            }
        }
    } catch (e) {
        console.error('Error fetching lobby states', e);
    }
}

async function fetchShopProducts() {
    if (!authToken || !shopProducts || shopProducts.children.length > 0) return;
    try {
        const resp = await fetch(`${SERVER_API_URL}/shop`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        if (!resp.ok) return;
        const data = await resp.json();
        shopProducts.innerHTML = '';
        data.products.forEach(product => {
            const item = document.createElement('div');
            item.className = 'shop-product';
            item.innerHTML = `
                <div>
                    <strong>${escapeHTML(product.title)}</strong>
                    <span>${escapeHTML(product.description)}</span>
                </div>
                <button class="shop-buy" data-product="${escapeHTML(product.id)}">${product.stars} STARS</button>
            `;
            shopProducts.appendChild(item);
        });

        shopProducts.querySelectorAll('.shop-buy').forEach(btn => {
            btn.addEventListener('click', () => buyShopProduct(btn.getAttribute('data-product')));
        });
    } catch (e) {
        console.error('Error fetching shop products', e);
    }
}

async function buyShopProduct(productId) {
    try {
        const resp = await fetch(`${SERVER_API_URL}/shop/stars-invoice`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ product_id: productId })
        });
        const data = await resp.json();
        if (!resp.ok) {
            showToast(data.detail || 'Payment unavailable', 'error');
            return;
        }

        const tg = window.Telegram?.WebApp;
        if (tg?.openInvoice) {
            tg.openInvoice(data.invoice_link, async (status) => {
                if (status === 'paid') {
                    showToast('Payment confirmed', 'success');
                    await fetchLobbyData();
                } else if (status === 'failed') {
                    showToast('Payment failed', 'error');
                }
            });
        } else {
            window.open(data.invoice_link, '_blank');
        }
    } catch (e) {
        showToast('Payment connection failed', 'error');
    }
}

btnDeposit.addEventListener('click', async () => {
    try {
        const resp = await fetch(`${SERVER_API_URL}/deposit`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ amount: 10.00 })
        });
        if (resp.ok) {
            await fetchLobbyData();
            showToast('+10 CR deposited', 'success');
        }
    } catch (e) {
        showToast('Connection failed', 'error');
    }
});


// ─── TOURNAMENT JOINING & WEBSOCKETS ───
async function joinTierTournament(tier) {
    try {
        const resp = await fetch(`${SERVER_API_URL}/join-tier`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ tier_id: tier })
        });

        const data = await resp.json();
        if (!resp.ok) {
            showToast(data.detail || 'Could not join tier', 'error');
            return;
        }

        activeTier = tier;
        showToast(`Entered Tier ${tier} Lobby`, 'success');
        clearInterval(tierStatsInterval);
        startWebGame(tier);

    } catch (e) {
        showToast('Network error joining lobby', 'error');
    }
}

function startWebGame(tier) {
    screenLobby.classList.add('hidden');
    screenGame.classList.remove('hidden');

    deathOverlay.classList.add('hidden');
    victoryOverlay.classList.add('hidden');

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Initialise coords
    selfX = WORLD_WIDTH / 2;
    selfY = WORLD_HEIGHT / 2;
    camX = selfX;
    camY = selfY;
    playersMap.clear();
    hazardZones = [];
    particles = [];
    bossState = { state: 'sleeping', x: 0, y: 0, time_remaining: 0, target_username: '' };

    // Register Key Listener
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);

    // Start WebSocket
    connectWebSocket();

    // Start Canvas Frame Loop
    lastFrameTime = performance.now();
    requestAnimationFrame(gameLoop);
}

function connectWebSocket() {
    wsConn = new WebSocket(SERVER_WS_URL);

    wsConn.onopen = () => {
        // Send Auth immediately
        wsConn.send(JSON.stringify({
            type: 'auth',
            token: authToken
        }));
        isConnected = true;
    };

    wsConn.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const mtype = data.type;

        if (mtype === 'welcome') {
            selfPlayerId = data.player_id;
            selfX = data.spawn_x;
            selfY = data.spawn_y;
            camX = selfX;
            camY = selfY;
            hudTier.textContent = `TIER ${activeTier}`;
        }
        else if (mtype === 'player_list') {
            const list = data.players;
            const activeIds = new Set();
            list.forEach(p => {
                if (p.id !== selfPlayerId) {
                    activeIds.add(p.id);
                    if (playersMap.has(p.id)) {
                        const existing = playersMap.get(p.id);
                        existing.targetX = p.x;
                        existing.targetY = p.y;
                        existing.is_alive = p.is_alive;
                    } else {
                        // New player: set start positions immediately, targets match
                        p.targetX = p.x;
                        p.targetY = p.y;
                        playersMap.set(p.id, p);
                    }
                } else {
                    // Sync alive status
                    if (!p.is_alive) {
                        triggerDeathOverlay('afk');
                    }
                }
            });

            // Remove players that disconnected
            for (let id of playersMap.keys()) {
                if (!activeIds.has(id)) {
                    playersMap.delete(id);
                }
            }

            // Update HUD counter
            const aliveCount = list.filter(p => p.is_alive).length;
            const totalCount = list.length;
            hudAlive.textContent = `ALIVE: ${aliveCount} / ${totalCount}`;
        }
        else if (mtype === 'boss_update') {
            bossState.state = data.boss.state;
            bossState.targetX = data.boss.x;
            bossState.targetY = data.boss.y;
            bossState.time_remaining = data.boss.time_remaining;
            bossState.target_username = data.boss.target_username;
            bossState.difficulty = data.boss.difficulty || 0;
            
            // On first wake, snap position directly to target to avoid sliding from (0,0)
            if (bossState.x === 0 && bossState.y === 0) {
                bossState.x = data.boss.x;
                bossState.y = data.boss.y;
            }
            updateBossHUD();
        }
        else if (mtype === 'chat_message') {
            addChatBubble(data.username, data.message);
        }
        else if (mtype === 'hazard_list') {
            hazardZones = data.hazards || [];
        }
        else if (mtype === 'player_eliminated') {
            addChatBubble('System', `${data.username} eliminated: ${formatDeathReason(data.reason)}`);
        }
        else if (mtype === 'game_won') {
            triggerVictoryOverlay(data.prize);
        }
        else if (mtype === 'you_died') {
            triggerDeathOverlay(data.reason);
        }
        else if (mtype === 'snap_back') {
            selfX = data.x;
            selfY = data.y;
        }
        else if (mtype === 'kicked') {
            wsConn.close();
            showToast('Logged in from another device', 'error');
            leaveGame();
        }
        else if (mtype === 'error') {
            showToast(data.message, 'error');
            leaveGame();
        }
    };

    wsConn.onclose = () => {
        isConnected = false;
    };
}

function leaveGame() {
    window.removeEventListener('resize', resizeCanvas);
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('keyup', onKeyUp);
    if (wsConn) {
        wsConn.close();
    }
    showLobbyScreen();
}

btnExitGame.onclick = leaveGame;
btnDeathExit.onclick = leaveGame;
btnVictoryExit.onclick = leaveGame;


// ─── GAME INPUTS & LOGIC ───
function onKeyDown(e) {
    if (document.activeElement === chatInput) return; // ignore when typing
    keysPressed[e.key.toLowerCase()] = true;

    // Chat activate
    if (e.key === 'Enter') {
        chatInput.focus();
    }
}

function onKeyUp(e) {
    keysPressed[e.key.toLowerCase()] = false;
}

chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const msg = chatInput.value.trim();
    chatInput.value = '';
    chatInput.blur(); // defocus

    if (msg && wsConn && isConnected) {
        wsConn.send(JSON.stringify({
            type: 'chat',
            message: msg
        }));
    }
});

// Touch controls virtual joystick
function setupJoystickEvents() {
    const base = document.getElementById('joystick-base');

    base.addEventListener('touchstart', (e) => {
        joystickActive = true;
        const rect = base.getBoundingClientRect();
        joystickStartPos = {
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2
        };
        handleJoystickMove(e.targetTouches[0]);
    });

    base.addEventListener('touchmove', (e) => {
        if (!joystickActive) return;
        handleJoystickMove(e.targetTouches[0]);
    });

    base.addEventListener('touchend', () => {
        joystickActive = false;
        joystickOffset = { x: 0, y: 0 };
        joystickHandle.style.transform = 'translate(-50%, -50%)';
    });
}

function handleJoystickMove(touch) {
    const dx = touch.clientX - joystickStartPos.x;
    const dy = touch.clientY - joystickStartPos.y;
    const dist = Math.min(50, Math.hypot(dx, dy));
    const angle = Math.atan2(dy, dx);

    const hx = dist * Math.cos(angle);
    const hy = dist * Math.sin(angle);

    joystickHandle.style.transform = `translate(calc(-50% + ${hx}px), calc(-50% + ${hy}px))`;

    // Normalise
    joystickOffset.x = hx / 50;
    joystickOffset.y = hy / 50;
}

// ─── DETERMINISTIC OBSTACLES ───
function generateObstacles() {
    obstacleList = [];
    // seed equivalent based on activeTier or simple sequence
    for (let i = 0; i < 40; i++) {
        // Pseudo-random deterministic placement
        const seedVal = Math.sin(i * 927.32) * 1000;
        const ox = 150 + Math.abs(seedVal % (WORLD_WIDTH - 300));
        const oy = 150 + Math.abs((seedVal * 1.5) % (WORLD_HEIGHT - 300));
        const rad = 25 + Math.abs((seedVal * 2.3) % 25);

        // Ensure spawning center is empty
        const cx = WORLD_WIDTH / 2;
        const cy = WORLD_HEIGHT / 2;
        const dist = Math.hypot(ox - cx, oy - cy);
        if (dist > 200) {
            obstacleList.push({ x: ox, y: oy, rad: rad });
        }
    }
}

// ─── RENDER ENGINE (HTML5 CANVAS) ───
function resizeCanvas() {
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
}

function gameLoop(now) {
    if (!screenGame.classList.contains('hidden')) {
        const dt = (now - lastFrameTime) / 1000; // in seconds
        lastFrameTime = now;

        update(dt);
        draw();

        requestAnimationFrame(gameLoop);
    }
}

function update(dt) {
    if (deathOverlay.classList.contains('hidden') && victoryOverlay.classList.contains('hidden')) {
        let dx = 0;
        let dy = 0;

        // Keyboard navigation
        if (keysPressed['w'] || keysPressed['arrowup']) dy -= 1;
        if (keysPressed['s'] || keysPressed['arrowdown']) dy += 1;
        if (keysPressed['a'] || keysPressed['arrowleft']) dx -= 1;
        if (keysPressed['d'] || keysPressed['arrowright']) dx += 1;

        // Joystick navigation override
        if (joystickActive) {
            dx = joystickOffset.x;
            dy = joystickOffset.y;
        }

        // Apply movement vector
        const mag = Math.hypot(dx, dy);
        if (mag > 0) {
            const speed = PLAYER_SPEED * dt;
            const moveX = (dx / mag) * speed;
            const moveY = (dy / mag) * speed;

            selfX += moveX;
            selfY += moveY;

            // Enforce Boundaries
            selfX = Math.max(15, Math.min(selfX, WORLD_WIDTH - 15));
            selfY = Math.max(15, Math.min(selfY, WORLD_HEIGHT - 15));

            // Colllision checks
            for (let obs of obstacleList) {
                const dist = Math.hypot(selfX - obs.x, selfY - obs.y);
                if (dist < obs.rad + PLAYER_RADIUS) {
                    const overlap = (obs.rad + PLAYER_RADIUS) - dist;
                    const pushX = ((selfX - obs.x) / dist) * overlap;
                    const pushY = ((selfY - obs.y) / dist) * overlap;
                    selfX += pushX;
                    selfY += pushY;
                }
            }

            // Spawn Particles
            if (particles.length < 200 && Math.random() < 0.25) {
                particles.push(generateParticle(selfX, selfY, COLORS.GRAY, 0.5));
            }

            // Throttled WebSocket Broadcast
            const nowMs = Date.now();
            if (nowMs - lastNetworkSend > netSendRate && wsConn && isConnected) {
                wsConn.send(JSON.stringify({
                    type: 'move',
                    x: selfX,
                    y: selfY
                }));
                lastNetworkSend = nowMs;
            }
        }
    }

    // Camera following target player smoothly
    camX += (selfX - camX) * CAMERA_SPEED * dt;
    camY += (selfY - camY) * CAMERA_SPEED * dt;

    // Boss Warning FX
    if (bossState.state === 'warning') {
        const pulse = Math.abs(Math.sin(Date.now() / 250));
        bossFlashScreen.style.opacity = (pulse * 0.4).toString();
        bossFlashScreen.classList.remove('hidden');
    } else if (bossState.state === 'hunting') {
        const pulse = Math.abs(Math.sin(Date.now() / 500));
        bossFlashScreen.style.opacity = (pulse * 0.25).toString();
        bossFlashScreen.classList.remove('hidden');

        // Spawn Boss Tracking Danger Particles
        if (particles.length < 300 && Math.random() < 0.5) {
            particles.push(generateParticle(bossState.x, bossState.y, COLORS.RED, 1.2));
        }
    } else {
        bossFlashScreen.classList.add('hidden');
    }

    // Update Particles
    particles = updateParticles(particles, dt);

    // Interpolate positions of other players for smooth movement
    playersMap.forEach(p => {
        if (p.targetX !== undefined && p.targetY !== undefined) {
            p.x += (p.targetX - p.x) * 15 * dt;
            p.y += (p.targetY - p.y) * 15 * dt;
        }
    });

    // Interpolate boss position for smooth movement
    if (bossState.state !== 'sleeping' && bossState.targetX !== undefined && bossState.targetY !== undefined) {
        bossState.x += (bossState.targetX - bossState.x) * 15 * dt;
        bossState.y += (bossState.targetY - bossState.y) * 15 * dt;
    }

    // Update daily moves check indicators
    if (userProfile) {
        updateDailyMovesHUD();
    }
}

function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Apply Screen Shake
    let camOffset = { x: 0, y: 0 };
    if (bossState.state === 'warning' || bossState.state === 'hunting') {
        const magnitude = bossState.state === 'hunting' ? 2 : 1;
        camOffset.x = (Math.random() - 0.5) * magnitude;
        camOffset.y = (Math.random() - 0.5) * magnitude;
    }

    const offsetX = canvas.width / 2 - (camX + camOffset.x);
    const offsetY = canvas.height / 2 - (camY + camOffset.y);

    // 1. Draw Viewport Grid Lines
    drawGrid(offsetX, offsetY);

    // 2. Draw World Boundaries
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 4;
    ctx.strokeRect(offsetX, offsetY, WORLD_WIDTH, WORLD_HEIGHT);

    // 3. Draw Obstacles
    ctx.fillStyle = '#222';
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 2;
    for (let obs of obstacleList) {
        ctx.beginPath();
        ctx.arc(obs.x + offsetX, obs.y + offsetY, obs.radius || obs.rad, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
    }

    // 4. Draw Particles
    drawParticles(offsetX, offsetY);

    // 5. Draw temporary hazard zones
    drawHazards(offsetX, offsetY);

    // 6. Draw Other Players
    playersMap.forEach(p => {
        if (p.is_alive) {
            drawCyberAvatar(p.x + offsetX, p.y + offsetY, COLORS.GRAY, false, p.username);
        } else {
            drawTombstone(p.x + offsetX, p.y + offsetY);
        }
    });

    // 7. Draw Self
    const selfAlive = !deathOverlay.classList.contains('hidden');
    if (!selfAlive) {
        drawCyberAvatar(selfX + offsetX, selfY + offsetY, COLORS.WHITE, true, username);
    } else {
        drawTombstone(selfX + offsetX, selfY + offsetY);
    }

    // 8. Draw Boss AI Void Creature
    if (bossState.state !== 'sleeping') {
        drawBoss(bossState.x + offsetX, bossState.y + offsetY);
    }

    // 9. Draw Minimap Overlay
    drawMinimap();
}

function drawGrid(ox, oy) {
    const gridSize = 80;
    ctx.strokeStyle = '#151515';
    ctx.lineWidth = 1;

    // Viewport bounds
    const startX = Math.max(0, Math.floor(-ox / gridSize) * gridSize);
    const endX = Math.min(WORLD_WIDTH, Math.ceil((canvas.width - ox) / gridSize) * gridSize);
    const startY = Math.max(0, Math.floor(-oy / gridSize) * gridSize);
    const endY = Math.min(WORLD_HEIGHT, Math.ceil((canvas.height - oy) / gridSize) * gridSize);

    for (let x = startX; x <= endX; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x + ox, startY + oy);
        ctx.lineTo(x + ox, endY + oy);
        ctx.stroke();
    }
    for (let y = startY; y <= endY; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(startX + ox, y + oy);
        ctx.lineTo(endX + ox, y + oy);
        ctx.stroke();
    }
}

// ─── CYBER HUMAN PILOT DRAWING ───
function drawCyberAvatar(x, y, baseColor, isSelf, uname) {
    const t = Date.now() / 1000;
    const bounce = Math.sin(t * 6.0) * 1.2;
    const swing = Math.sin(t * 10.0) * 3.5;

    // Backing subtle neon glow
    ctx.beginPath();
    ctx.arc(x, y, 12 + bounce, 0, Math.PI*2);
    ctx.fillStyle = isSelf ? 'rgba(255,255,255,0.06)' : 'rgba(136,136,136,0.06)';
    ctx.fill();

    // Floor Target Selector
    if (isSelf) {
        ctx.beginPath();
        ctx.ellipse(x, y + 12, 14 + Math.sin(t * 8.0) * 1, 5, 0, 0, Math.PI * 2);
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();

        // Arrow indicator pointing down above head
        const arrowY = y - 22 + Math.sin(t * 12.0) * 1.5;
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.moveTo(x - 4, arrowY - 4);
        ctx.lineTo(x + 4, arrowY - 4);
        ctx.lineTo(x, arrowY);
        ctx.closePath();
        ctx.fill();
    }

    // Cyber Power Cell on Back
    ctx.fillStyle = COLORS.DARK_GRAY;
    ctx.fillRect(x - 8, y - 3 + bounce, 3, 9);
    ctx.fillStyle = baseColor;
    ctx.fillRect(x - 8, y - 1 + bounce, 1.5, 5);

    // Torso plates
    ctx.fillStyle = baseColor;
    ctx.fillRect(x - 5, y - 3 + bounce, 10, 12);

    // Glowing reactor core
    ctx.beginPath();
    ctx.arc(x, y + 2 + bounce, 1.8, 0, Math.PI * 2);
    ctx.fillStyle = isSelf ? COLORS.GREEN : COLORS.ORANGE;
    ctx.fill();

    // Shoulders
    const padCol = isSelf ? COLORS.WHITE : COLORS.GRAY;
    ctx.fillStyle = padCol;
    ctx.beginPath(); ctx.arc(x - 6, y - 2 + bounce, 1.5, 0, Math.PI*2); ctx.fill();
    ctx.beginPath(); ctx.arc(x + 6, y - 2 + bounce, 1.5, 0, Math.PI*2); ctx.fill();

    // Cyber Helmet
    const headY = y - 9 + bounce * 0.75;
    ctx.fillStyle = baseColor;
    ctx.beginPath();
    ctx.arc(x, headY, 5.5, 0, Math.PI * 2);
    ctx.fill();

    // Glowing Helmet Visor
    ctx.strokeStyle = isSelf ? COLORS.GREEN : COLORS.RED;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - 4, headY - 1);
    ctx.lineTo(x + 2, headY - 1);
    ctx.stroke();

    // Comms Antenna
    ctx.strokeStyle = COLORS.DARK_GRAY;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + 3, headY - 3);
    ctx.lineTo(x + 5, headY - 9);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x + 5, headY - 9, 1, 0, Math.PI*2);
    ctx.fillStyle = isSelf ? COLORS.GREEN : COLORS.RED;
    ctx.fill();

    // Swaying Legs & Boots
    ctx.strokeStyle = baseColor;
    ctx.lineWidth = 2;
    // Left Leg
    ctx.beginPath();
    ctx.moveTo(x - 3, y + 9 + bounce);
    ctx.lineTo(x - 5 + swing, y + 17);
    ctx.stroke();
    ctx.fillStyle = COLORS.DARK_GRAY;
    ctx.fillRect(x - 7 + swing, y + 16, 3, 2);

    // Right Leg
    ctx.beginPath();
    ctx.moveTo(x + 3, y + 9 + bounce);
    ctx.lineTo(x + 5 - swing, y + 17);
    ctx.stroke();
    ctx.fillStyle = COLORS.DARK_GRAY;
    ctx.fillRect(x + 4 - swing, y + 16, 3, 2);

    // Swaying Arms
    ctx.strokeStyle = baseColor;
    ctx.beginPath();
    ctx.moveTo(x - 5, y - 1 + bounce);
    ctx.lineTo(x - 8 - swing * 0.5, y + 7 + bounce);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + 5, y - 1 + bounce);
    ctx.lineTo(x + 8 + swing * 0.5, y + 7 + bounce);
    ctx.stroke();

    // Render Username Label
    if (uname) {
        ctx.fillStyle = isSelf ? '#fff' : COLORS.WHITE;
        ctx.font = '700 10px Inter';
        ctx.textAlign = 'center';
        ctx.fillText(uname, x, y - 28);
    }
}

function drawTombstone(x, y) {
    ctx.fillStyle = COLORS.GRAY;
    ctx.fillRect(x - 9, y - 12, 18, 24);
    // rounded top
    ctx.beginPath();
    ctx.arc(x, y - 12, 9, 0, Math.PI, true);
    ctx.fill();

    // RIP Cross
    ctx.strokeStyle = COLORS.DARK_GRAY;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y - 16); ctx.lineTo(x, y - 4);
    ctx.moveTo(x - 5, y - 12); ctx.lineTo(x + 5, y - 12);
    ctx.stroke();
}

// ─── MENACING BOSS VOID CREATURE DRAWING ───
function drawBoss(x, y) {
    const t = Date.now() / 1000;
    
    // Glowing Outer Halo
    const glowRadius = BOSS_RADIUS * (1.3 + 0.15 * Math.sin(t * 8.0));
    const grad = ctx.createRadialGradient(x, y, BOSS_RADIUS * 0.6, x, y, glowRadius);
    grad.addColorStop(0, 'rgba(255, 34, 0, 0.4)');
    grad.addColorStop(1, 'rgba(255, 34, 0, 0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(x, y, glowRadius, 0, Math.PI*2);
    ctx.fill();

    // Jagged Polygon Void Core
    const pointsCount = 12;
    ctx.fillStyle = '#000';
    ctx.strokeStyle = COLORS.RED;
    ctx.lineWidth = 3;
    ctx.beginPath();
    
    for (let i = 0; i < pointsCount; i++) {
        const angle = (i / pointsCount) * Math.PI * 2;
        const offset = 6 * Math.sin(t * 12.0 + i * 1.5);
        const r = BOSS_RADIUS + offset;
        const px = x + r * Math.cos(angle);
        const py = y + r * Math.sin(angle);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Menacing Glowing Eyes
    const eyeOffset = 12;
    const eyeSize = 4 + Math.sin(t * 5.0) * 1.5;
    ctx.fillStyle = COLORS.RED;
    ctx.beginPath(); ctx.arc(x - eyeOffset, y - 4, eyeSize, 0, Math.PI*2); ctx.fill();
    ctx.beginPath(); ctx.arc(x + eyeOffset, y - 4, eyeSize, 0, Math.PI*2); ctx.fill();
}

function drawHazards(ox, oy) {
    const t = Date.now() / 1000;
    hazardZones.forEach(h => {
        const pulse = 0.75 + Math.sin(t * 5 + h.id) * 0.15;
        ctx.beginPath();
        ctx.arc(h.x + ox, h.y + oy, h.radius * pulse, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(204, 0, 0, 0.12)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(204, 0, 0, 0.65)';
        ctx.lineWidth = 2;
        ctx.setLineDash([8, 8]);
        ctx.stroke();
        ctx.setLineDash([]);
    });
}

// ─── RADAR MINIMAP OVERLAY ───
function drawMinimap() {
    const mapSize = 120;
    const margin = 15;
    
    const mx = canvas.width - mapSize - margin;
    const my = margin;

    // Panel
    ctx.fillStyle = COLORS.PANEL_BG;
    ctx.strokeStyle = COLORS.PANEL_BORDER;
    ctx.lineWidth = 1;
    ctx.fillRect(mx, my, mapSize, mapSize);
    ctx.strokeRect(mx, my, mapSize, mapSize);

    const scale = mapSize / WORLD_WIDTH;

    // Draw Obstacles on map
    ctx.fillStyle = '#1f1f1f';
    for (let obs of obstacleList) {
        ctx.beginPath();
        ctx.arc(mx + obs.x * scale, my + obs.y * scale, obs.rad * scale, 0, Math.PI * 2);
        ctx.fill();
    }

    // Draw Other players
    playersMap.forEach(p => {
        const px = mx + p.x * scale;
        const py = my + p.y * scale;
        if (px >= mx && px <= mx + mapSize && py >= my && py <= my + mapSize) {
            ctx.fillStyle = p.is_alive ? COLORS.GRAY : '#444';
            ctx.beginPath();
            ctx.arc(px, py, p.is_alive ? 2 : 1, 0, Math.PI*2);
            ctx.fill();
        }
    });

    // Draw Self Dot
    const selfPx = mx + selfX * scale;
    const selfPy = my + selfY * scale;
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(selfPx, selfPy, 2.5, 0, Math.PI * 2);
    ctx.fill();

    // Draw Boss Danger Radar
    if (bossState.state !== 'sleeping') {
        const bx = mx + bossState.x * scale;
        const by = my + bossState.y * scale;
        const pulse = Math.abs(Math.sin(Date.now() / 150));
        ctx.fillStyle = COLORS.RED;
        ctx.beginPath();
        ctx.arc(bx, by, 4 + pulse * 2, 0, Math.PI * 2);
        ctx.fill();
    }

    // Draw Camera bounds rectangle
    const viewW = canvas.width * scale;
    const viewH = canvas.height * scale;
    const rx = mx + (camX - canvas.width / 2) * scale;
    const ry = my + (camY - canvas.height / 2) * scale;
    
    // clamp
    const drawRx = Math.max(mx, Math.min(rx, mx + mapSize - viewW));
    const drawRy = Math.max(my, Math.min(ry, my + mapSize - viewH));

    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    ctx.strokeRect(drawRx, drawRy, viewW, viewH);
}


// ─── PARTICLE ENGINE ───
function generateParticle(x, y, colorCode, speedMult = 1.0) {
    const angle = Math.random() * Math.PI * 2;
    const mag = (0.5 + Math.random() * 2.5) * speedMult;
    return {
        x: x,
        y: y,
        vx: mag * Math.cos(angle),
        vy: mag * Math.sin(angle),
        color: colorCode,
        alpha: 1.0,
        size: 2 + Math.floor(Math.random() * 3),
        life: 0.3 + Math.random() * 0.5 // duration in seconds
    };
}

function updateParticles(pList, dt) {
    const alive = [];
    for (let p of pList) {
        p.life -= dt;
        if (p.life > 0) {
            p.x += p.vx;
            p.y += p.vy;
            p.vx *= 0.95;
            p.vy *= 0.95;
            p.alpha = Math.max(0, p.life / 0.8);
            alive.push(p);
        }
    }
    return alive;
}

function drawParticles(ox, oy) {
    for (let p of particles) {
        ctx.fillStyle = p.color;
        ctx.globalAlpha = p.alpha;
        ctx.beginPath();
        ctx.arc(p.x + ox, p.y + oy, p.size, 0, Math.PI*2);
        ctx.fill();
    }
    ctx.globalAlpha = 1.0; // reset
}


// ─── HUD UI UTILITIES ───
function updateBossHUD() {
    const title = document.getElementById('boss-alert-title');
    const subtitle = document.getElementById('boss-alert-subtitle');
    const banner = document.getElementById('boss-alert-banner');

    if (bossState.state === 'warning') {
        title.textContent = `BOSS IS WAKING IN ${bossState.time_remaining}s!`;
        title.style.color = COLORS.RED;
        subtitle.textContent = 'PREPARE TO RUN OR HIDE IMMEDIATELY';
        banner.classList.remove('hidden');
    } else if (bossState.state === 'hunting') {
        title.textContent = '⚠️ WARNING: BOSS ACTIVE & HUNTING!';
        title.style.color = '#fff';
        const diff = bossState.difficulty ? ` | THREAT ${bossState.difficulty}` : '';
        subtitle.textContent = bossState.target_username ? `CURRENT TARGET: ${bossState.target_username.toUpperCase()}${diff}` : `SEEKING VICTIMS${diff}`;
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

function updateDailyMovesHUD() {
    const count = userProfile.daily_moves_today || 0;
    dot1.className = count > 0 ? 'dot filled' : 'dot';
    dot2.className = count > 1 ? 'dot filled' : 'dot';

    hudMovesLabel.textContent = `${Math.min(count, 2)}/2 moves done`;
    hudMovesLabel.className = count >= 2 ? '' : 'warning';
}

function addChatBubble(user, text) {
    const bubble = document.createElement('div');
    bubble.className = 'chat-msg';
    bubble.innerHTML = `<span>${escapeHTML(user)}:</span> ${escapeHTML(text)}`;
    chatMessages.appendChild(bubble);

    // Scroll bottom
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Prune old bubbles (max 10)
    while (chatMessages.children.length > 15) {
        chatMessages.removeChild(chatMessages.firstChild);
    }
}

function triggerDeathOverlay(reason) {
    if (wsConn) wsConn.close();
    
    let text = 'Eliminated by the Boss.';
    if (reason === 'afk') text = 'Eliminated: Failed AFK daily moves quota.';
    else if (reason === 'hazard') text = 'Eliminated: Stood inside a danger zone.';
    else if (reason === 'kicked') text = 'Logged in from another location.';

    deathReason.textContent = text;
    deathOverlay.classList.remove('hidden');
}

function formatDeathReason(reason) {
    if (reason === 'boss') return 'Boss';
    if (reason === 'afk') return 'AFK';
    if (reason === 'hazard') return 'Hazard';
    return reason || 'Unknown';
}

function triggerVictoryOverlay(prize) {
    if (wsConn) wsConn.close();
    victoryPrize.textContent = `${prize.toFixed(2)} CR`;
    victoryOverlay.classList.remove('hidden');
}

// ─── GENERAL HELPERS ───
function showToast(text, type = 'success') {
    toastAlert.textContent = text;
    toastAlert.className = `toast ${type}`;
    toastAlert.classList.remove('hidden');
    
    setTimeout(() => {
        toastAlert.classList.add('hidden');
    }, 3000);
}

function escapeHTML(str) {
    return str.replace(/[&<>'"]/g, 
        tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
}
