import pygame
import random
import math
import time
from typing import List, Dict, Tuple, Any
from config import COLORS, PLAYER_RADIUS, BOSS_RADIUS, WORLD_WIDTH, WORLD_HEIGHT, MINIMAP_WIDTH, MINIMAP_HEIGHT, MINIMAP_MARGIN

# ─── Surface Caching ──────────────────────────────────────────────────────────
_surface_cache: Dict[Tuple, pygame.Surface] = {}

def get_cached_circle_surface(color: Tuple[int, int, int], size: int, alpha: int) -> pygame.Surface:
    """Returns a cached transparent circle surface to prevent per-frame allocations."""
    q_alpha = (alpha // 8) * 8  # Quantize to reduce memory footprint
    key = ("circle", color, size, q_alpha)
    if key not in _surface_cache:
        surf = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, (color[0], color[1], color[2], q_alpha), (size, size), size)
        _surface_cache[key] = surf
    return _surface_cache[key]

def get_cached_glow_surface(color: Tuple[int, int, int], radius: int) -> pygame.Surface:
    """Returns a cached player backing glow surface."""
    key = ("glow", color, radius)
    if key not in _surface_cache:
        size = radius * 2
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(surf, (color[0], color[1], color[2], 25), (radius, radius), radius)
        _surface_cache[key] = surf
    return _surface_cache[key]

def get_cached_boss_glow_surface(color: Tuple[int, int, int], radius: int) -> pygame.Surface:
    """Returns a cached boss aura glow surface."""
    key = ("boss_glow", color, radius)
    if key not in _surface_cache:
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        for r in range(radius, 0, -2):
            alpha = int(60 * (1.0 - (r / radius)))
            pygame.draw.circle(surf, (color[0], color[1], color[2], alpha), (radius, radius), r)
        _surface_cache[key] = surf
    return _surface_cache[key]


def draw_player(
    surface: pygame.Surface,
    x: float,
    y: float,
    color: Tuple[int, int, int],
    is_self: bool = False,
    username: str = "",
    is_alive: bool = True,
    font: pygame.font.Font = None
):
    """Draws a highly stylized, animated cyber-humanoid figure representing a player."""
    if not is_alive:
        # Draw a tombstone
        rect_width = 18
        rect_height = 24
        pygame.draw.rect(
            surface,
            COLORS["GRAY"],
            (x - rect_width // 2, y - rect_height // 2, rect_width, rect_height),
            border_radius=4
        )
        pygame.draw.line(surface, COLORS["DARK_GRAY"], (x, y - 6), (x, y + 6), 2)
        pygame.draw.line(surface, COLORS["DARK_GRAY"], (x - 5, y - 2), (x + 5, y - 2), 2)
        return

    # Idle breathing and walk-swing cycles (active for all players to feel alive)
    t = time.time()
    bounce = math.sin(t * 6.0) * 1.2
    arm_swing = math.sin(t * 10.0) * 3.5

    # 1. Backing glow
    glow_radius = 12 + int(bounce)
    glow_surf = get_cached_glow_surface(color, glow_radius)
    surface.blit(glow_surf, (int(x - glow_radius), int(y - glow_radius)))

    # 2. Cyber floor ring (under player feet)
    if is_self:
        ring_color = COLORS["WHITE"]
        ring_w = 28 + int(math.sin(t * 8.0) * 2)
        ring_h = 10
        pygame.draw.ellipse(surface, ring_color, (int(x - ring_w // 2), int(y + 12), ring_w, ring_h), 1)

    # 3. Cyber backpack / power cell (left-side offset)
    pygame.draw.rect(surface, COLORS["DARK_GRAY"], (int(x - 8), int(y - 3 + bounce), 4, 10), border_radius=1)
    pygame.draw.rect(surface, color, (int(x - 8), int(y - 1 + bounce), 2, 6), border_radius=1)

    # 4. Body torso/armor chestplate
    pygame.draw.rect(surface, color, (int(x - 5), int(y - 3 + bounce), 10, 13), border_radius=3)
    # Chest detail reactor core (pulsing glowing dot)
    core_color = COLORS["GREEN"] if is_self else COLORS["ORANGE"]
    pygame.draw.circle(surface, core_color, (int(x), int(y + 2 + bounce)), 2)

    # 5. Shoulder Pads
    pad_color = COLORS["WHITE"] if is_self else COLORS["GRAY"]
    pygame.draw.circle(surface, pad_color, (int(x - 6), int(y - 2 + bounce)), 2)
    pygame.draw.circle(surface, pad_color, (int(x + 6), int(y - 2 + bounce)), 2)

    # 6. Head / cyber-helmet
    head_y = int(y - 9 + bounce * 0.7)
    pygame.draw.circle(surface, color, (int(x), head_y), 6)
    
    # Helmet Visor (glowing neon line)
    visor_col = COLORS["GREEN"] if is_self else COLORS["RED"]
    pygame.draw.line(surface, visor_col, (int(x - 4), head_y - 1), (int(x + 2), head_y - 1), 2)

    # High-tech Comm Antenna
    pygame.draw.line(surface, COLORS["DARK_GRAY"], (int(x + 3), head_y - 4), (int(x + 5), head_y - 10), 1)
    pygame.draw.circle(surface, visor_col, (int(x + 5), head_y - 10), 1)

    # 7. Limbs (animated legs)
    pygame.draw.line(surface, color, (int(x - 3), int(y + 9 + bounce)), (int(x - 5 + arm_swing), int(y + 17)), 2)
    pygame.draw.line(surface, color, (int(x + 3), int(y + 9 + bounce)), (int(x + 5 - arm_swing), int(y + 17)), 2)
    
    # Boots
    pygame.draw.rect(surface, COLORS["DARK_GRAY"], (int(x - 7 + arm_swing), int(y + 16), 3, 2))
    pygame.draw.rect(surface, COLORS["DARK_GRAY"], (int(x + 4 - arm_swing), int(y + 16), 3, 2))

    # 8. Arms
    pygame.draw.line(surface, color, (int(x - 5), int(y - 1 + bounce)), (int(x - 8 - arm_swing * 0.5), int(y + 7 + bounce)), 2)
    pygame.draw.line(surface, color, (int(x + 5), int(y - 1 + bounce)), (int(x + 8 + arm_swing * 0.5), int(y + 7 + bounce)), 2)

    # Indicator pointer triangle above head for self
    if is_self:
        t_y = int(y - 22 + math.sin(t * 12.0) * 1.5)
        pygame.draw.polygon(surface, COLORS["WHITE"], [(x - 4, t_y - 4), (x + 4, t_y - 4), (x, t_y)])

    # Draw username label
    if username and font:
        txt = font.render(username, True, COLORS["ACCENT"] if not is_self else COLORS["WHITE"])
        rect = txt.get_rect(center=(int(x), int(y - 32)))
        surface.blit(txt, rect)


def draw_boss(surface: pygame.Surface, x: float, y: float, pulse_time: float = 0.0):
    """Draws a menacing boss entity with shifting, jagged edges and a glowing aura."""
    # Outer glow
    glow_radius = int(BOSS_RADIUS * (1.3 + 0.15 * math.sin(pulse_time * 8.0)))
    glow_surf = get_cached_boss_glow_surface(COLORS["BOSS_GLOW"], glow_radius)
    surface.blit(glow_surf, (int(x - glow_radius), int(y - glow_radius)))

    # Main core - procedural jagged polygon representing a black void creature
    points = []
    num_spikes = 12
    for i in range(num_spikes):
        angle = (i / num_spikes) * 2 * math.pi
        # Vary radius randomly based on time to create a "shifting" effect
        offset = 6 * math.sin(pulse_time * 12.0 + i * 1.5)
        rad = BOSS_RADIUS + offset
        px = x + rad * math.cos(angle)
        py = y + rad * math.sin(angle)
        points.append((px, py))

    pygame.draw.polygon(surface, COLORS["BLACK"], points)
    pygame.draw.polygon(surface, COLORS["BOSS_COLOR"], points, 3)

    # Glowing eyes
    eye_offset = 12
    eye_size = 4 + int(2 * abs(math.sin(pulse_time * 5.0)))
    pygame.draw.circle(surface, COLORS["RED"], (int(x - eye_offset), int(y - 4)), eye_size)
    pygame.draw.circle(surface, COLORS["RED"], (int(x + eye_offset), int(y - 4)), eye_size)


def create_crosshatch_background(width: int, height: int, grid_size: int = 80) -> pygame.Surface:
    """Generates a tileable background surface with a dark grid and crosshatch layout."""
    surf = pygame.Surface((width, height))
    surf.fill(COLORS["BG"])
    
    # Draw grid lines
    for x in range(0, width, grid_size):
        pygame.draw.line(surf, COLORS["DARK_GRAY"], (x, 0), (x, height), 1)
    for y in range(0, height, grid_size):
        pygame.draw.line(surf, COLORS["DARK_GRAY"], (0, y), (width, y), 1)

    # Subtle decorative dots/crosses at intersections
    for x in range(0, width, grid_size):
        for y in range(0, height, grid_size):
            pygame.draw.line(surf, COLORS["GRAY"], (x - 3, y), (x + 3, y), 1)
            pygame.draw.line(surf, COLORS["GRAY"], (x, y - 3), (x, y + 3), 1)

    return surf


# ─────────────────────────────────────────────────────────────────────────────
# Particle System
# ─────────────────────────────────────────────────────────────────────────────

def generate_particle(x: float, y: float, color: Tuple[int,int,int], speed: float = 1.0) -> Dict[str, Any]:
    """Spawns a single particle dictionary."""
    angle = random.uniform(0, 2 * math.pi)
    mag = random.uniform(0.5, 3.0) * speed
    return {
        "x": x,
        "y": y,
        "vx": mag * math.cos(angle),
        "vy": mag * math.sin(angle),
        "color": color,
        "alpha": 255,
        "size": random.randint(2, 5),
        "life": random.uniform(0.3, 0.8)  # duration in seconds
    }


def update_particles(particles: List[Dict[str, Any]], dt: float) -> List[Dict[str, Any]]:
    """Updates and prunes particles based on elapsed time."""
    alive = []
    for p in particles:
        p["life"] -= dt
        if p["life"] > 0:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            # slow down slightly (drag)
            p["vx"] *= 0.95
            p["vy"] *= 0.95
            # fade alpha
            p["alpha"] = int((p["life"] / 0.8) * 255)
            p["alpha"] = max(0, min(255, p["alpha"]))
            alive.append(p)
    return alive


def draw_particles(surface: pygame.Surface, particles: List[Dict[str, Any]]):
    """Draws active particles using alpha blends."""
    for p in particles:
        if p["alpha"] <= 0:
            continue
        size = int(p["size"])
        if size < 1:
            size = 1
        psurf = get_cached_circle_surface(p["color"], size, p["alpha"])
        surface.blit(psurf, (int(p["x"] - size), int(p["y"] - size)))


# ─────────────────────────────────────────────────────────────────────────────
# Screen Overlays & Minimap
# ─────────────────────────────────────────────────────────────────────────────

_warning_overlay_surf = None

def draw_boss_warning_overlay(surface: pygame.Surface, alpha: float):
    """Renders a full-screen red flashing border indicating Boss waking warning."""
    global _warning_overlay_surf
    if alpha <= 0:
        return
    size = surface.get_size()
    if _warning_overlay_surf is None or _warning_overlay_surf.get_size() != size:
        _warning_overlay_surf = pygame.Surface(size, pygame.SRCALPHA)
    
    _warning_overlay_surf.fill((0, 0, 0, 0))
    # Red borders
    border_thick = 16
    rect = surface.get_rect()
    pygame.draw.rect(_warning_overlay_surf, (204, 0, 0, int(alpha)), rect, border_thick)
    # Subtle red transparent overlay
    pygame.draw.rect(_warning_overlay_surf, (204, 0, 0, int(alpha * 0.1)), rect)
    surface.blit(_warning_overlay_surf, (0, 0))


def draw_minimap(
    surface: pygame.Surface,
    players: List[Dict[str, Any]],
    boss_state: Dict[str, Any],
    camera_x: float,
    camera_y: float,
    self_player_id: int
):
    """Renders a simplified radar/minimap HUD element in the corner."""
    scr_w, scr_h = surface.get_size()
    map_x = scr_w - MINIMAP_WIDTH - MINIMAP_MARGIN
    map_y = MINIMAP_MARGIN

    # Draw minimap container
    pygame.draw.rect(surface, COLORS["PANEL_BG"], (map_x, map_y, MINIMAP_WIDTH, MINIMAP_HEIGHT))
    pygame.draw.rect(surface, COLORS["PANEL_BORDER"], (map_x, map_y, MINIMAP_WIDTH, MINIMAP_HEIGHT), 2)

    # Scale factors
    scale_x = MINIMAP_WIDTH / WORLD_WIDTH
    scale_y = MINIMAP_HEIGHT / WORLD_HEIGHT

    # Draw boundary map limits
    # Draw rocks/pillars placeholder on minimap if needed
    
    # Draw players
    for p in players:
        px = map_x + int(p["x"] * scale_x)
        py = map_y + int(p["y"] * scale_y)
        
        # Check if coordinates inside minimap bounds (should be)
        if map_x <= px <= map_x + MINIMAP_WIDTH and map_y <= py <= map_y + MINIMAP_HEIGHT:
            if not p.get("is_alive", True):
                # Small gray cross
                pygame.draw.line(surface, COLORS["GRAY"], (px - 2, py - 2), (px + 2, py + 2), 1)
                pygame.draw.line(surface, COLORS["GRAY"], (px - 2, py + 2), (px + 2, py - 2), 1)
            else:
                color = COLORS["WHITE"] if p["id"] == self_player_id else COLORS["GRAY"]
                size = 3 if p["id"] == self_player_id else 2
                pygame.draw.circle(surface, color, (px, py), size)

    # Draw Boss
    if boss_state and boss_state.get("state") != "sleeping":
        bx = map_x + int(boss_state.get("x", 0.0) * scale_x)
        by = map_y + int(boss_state.get("y", 0.0) * scale_y)
        if map_x <= bx <= map_x + MINIMAP_WIDTH and map_y <= by <= map_y + MINIMAP_HEIGHT:
            # Flashing red circle
            pulse = abs(math.sin(pygame.time.get_ticks() / 150.0))
            b_size = 5 + int(3 * pulse)
            pygame.draw.circle(surface, COLORS["RED"], (bx, by), b_size)
            pygame.draw.circle(surface, COLORS["WHITE"], (bx, by), b_size, 1)

    # Draw camera view rectangle bounds
    view_w_px = (scr_w * scale_x)
    view_h_px = (scr_h * scale_y)
    view_x = map_x + int((camera_x - scr_w / 2) * scale_x)
    view_y = map_y + int((camera_y - scr_h / 2) * scale_y)
    
    # Clip rectangle
    rx = max(map_x, min(view_x, map_x + MINIMAP_WIDTH - int(view_w_px)))
    ry = max(map_y, min(view_y, map_y + MINIMAP_HEIGHT - int(view_h_px)))
    pygame.draw.rect(surface, COLORS["WHITE"], (rx, ry, int(view_w_px), int(view_h_px)), 1)
