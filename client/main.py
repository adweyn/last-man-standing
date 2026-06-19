"""
main.py — Main game entry point.
Initialises Pygame window environment, manages global scene routing, and handles cleanup.
"""

import sys
import pygame
import logging
from config import SCREEN_WIDTH, SCREEN_HEIGHT, FPS
from network import NetworkManager
from menu import MainMenu
from game import GameplayScreen

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("LMS_CLIENT")


def main():
    logger.info("Initializing Last Man Standing game client...")
    
    pygame.init()
    pygame.mixer.init()  # Initialize audio engine in case sound needs to be added later
    from sound import init_synth_sounds
    init_synth_sounds()

    # Create Pygame window
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Last Man Standing")

    # Load custom icon if available, otherwise draw a default geometric shape
    try:
        icon_surf = pygame.Surface((32, 32))
        icon_surf.fill((10, 10, 10))
        pygame.draw.circle(icon_surf, (255, 0, 0), (16, 16), 12)
        pygame.display.set_icon(icon_surf)
    except Exception:
        pass

    # Instantiate global network manager
    network = NetworkManager()

    # Scene state control: "MENU" | "GAME"
    current_scene = "MENU"

    # Instantiate menu
    menu_scene = MainMenu(screen, network)
    game_scene = None

    clock = pygame.time.Clock()
    running = True

    logger.info("Entering main game loop...")
    while running:
        # Calculate frame delta time in seconds
        dt = clock.tick(FPS) / 1000.0
        
        # Limit delta time spikes (e.g. if dragging the window) to prevent physics jumps
        dt = min(0.1, dt)

        # ─── EVENT HANDLING ──────────────────────────────────────────────────────
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                running = False
                break

            if current_scene == "MENU":
                tier_joined = menu_scene.handle_event(event)
                if tier_joined is not None:
                    # Successfully entered a tier tournament! Transition to active game screen.
                    logger.info(f"Transitioning to gameplay scene for Tier {tier_joined}")
                    game_scene = GameplayScreen(screen, network, tier_joined)
                    current_scene = "GAME"
            
            elif current_scene == "GAME":
                should_exit = game_scene.handle_event(event)
                if should_exit:
                    # Player chose to exit to menu (disconnected/died)
                    logger.info("Transitioning back to main menu...")
                    current_scene = "MENU"
                    # Refresh menu data
                    menu_scene.state = "tier_select"
                    menu_scene.refresh_lobby_data()
                    game_scene = None

        if not running:
            break

        # ─── TICK STATE UPDATES ──────────────────────────────────────────────────
        if current_scene == "MENU":
            menu_scene.update(dt)
        elif current_scene == "GAME":
            game_scene.update(dt)

        # ─── SCREEN RENDERING ────────────────────────────────────────────────────
        if current_scene == "MENU":
            menu_scene.draw()
        elif current_scene == "GAME":
            game_scene.draw()

        pygame.display.flip()

    # ─── SHUTDOWN & CLEANUP ──────────────────────────────────────────────────
    logger.info("Shutting down client components...")
    network.disconnect()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
