import os
import time
import math
import asyncio
import random
from mitmproxy import http, ctx

def get_high_score():
    try:
        score_path = os.path.join(os.path.dirname(__file__), 'highscore.txt')
        if os.path.exists(score_path):
            with open(score_path, 'r') as f:
                return int(f.read().strip())
    except Exception as e:
        print(f"Error reading highscore: {e}")
    return 0

def set_high_score(score):
    try:
        score_path = os.path.join(os.path.dirname(__file__), 'highscore.txt')
        with open(score_path, 'w') as f:
            f.write(str(score))
    except Exception as e:
        print(f"Error writing highscore: {e}")

# Configuration
LOCAL_JS_PATH = "slither_engine.js"

# Global Game State
MY_SNAKE_ID = None
MY_X = 0
MY_Y = 0
MY_SCORE = 0
MY_MAX_SCORE = 0
MY_SCORE_STR = "0"
HITBOXES = [] # format: (x, y, snake_id)
ENEMY_HEADS = {} # format: snake_id -> (x, y)
FOODS = {}    # format: food_id -> (x, y, sz)
SNAKE_NAMES = {} # format: snake_id -> "nickname"
MSL = 42 # Default movement segment length
LFVSX = 0
LFVSY = 0

def point_to_segment_dist(px, py, ax, ay, bx, by):
    l2 = (ax - bx)**2 + (ay - by)**2
    if l2 == 0:
        return math.sqrt((px - ax)**2 + (py - ay)**2)
    t = max(0, min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / l2))
    proj_x = ax + t * (bx - ax)
    proj_y = ay + t * (by - ay)
    return math.sqrt((px - proj_x)**2 + (py - proj_y)**2)

def request(flow: http.HTTPFlow) -> None:
    """Intercepts and serves the local engine file."""
    if "slither.io/s/game" in flow.request.pretty_url and flow.request.pretty_url.endswith(".js"):
        if os.path.exists(LOCAL_JS_PATH):
            with open(LOCAL_JS_PATH, "rb") as f:
                local_js = f.read()
            flow.response = http.Response.make(
                200, local_js, {"Content-Type": "application/javascript", "Cache-Control": "no-cache"}
            )
            print("[*] Intercepted slither_engine.js and served local copy.")

async def ai_loop(flow: http.HTTPFlow):
    """Background AI loop that continuously injects steering commands."""
    print("[AI] Food-seeking AI started.")
    
    target_food_id = None
    target_food_start_time = time.time()
    ignored_zones = [] # List of tuples: (x, y, expiration_time)
    current_ai_state = "SEEKING"
    
    try:
        while getattr(flow, 'has_spawned', False):
            auto_angle = 0
            current_time = time.time()
            
            # Find the nearest/best food
            target_x, target_y = None, None
            best_food_id = None
            
            # --- BLOODLUST EVALUATION ---
            BLOODLUST_ENABLED = False # Disabled per user request
            best_bloodlust_food = None
            best_bloodlust_dist = float('inf')
            
            if BLOODLUST_ENABLED and getattr(flow, 'bloodlust_until', 0) > current_time:
                # Still in bloodlust, check if food still exists
                target_f_id = getattr(flow, 'bloodlust_food_id', None)
                if target_f_id in FOODS:
                    fx, fy, sz = FOODS[target_f_id]
                    my_dist_sq = (fx - MY_X)**2 + (fy - MY_Y)**2
                    is_closest = True
                    for sid, (ex, ey) in ENEMY_HEADS.items():
                        edist_sq = (fx - ex)**2 + (fy - ey)**2
                        if edist_sq < my_dist_sq:
                            is_closest = False
                            break
                    if is_closest:
                        best_bloodlust_food = (fx, fy, target_f_id)
                    else:
                        flow.bloodlust_until = 0
                        print(f"[{MY_SCORE_STR}] ❌ BLOODLUST ABORTED: Enemy got closer to the target food!")
                else:
                    flow.bloodlust_until = 0
            
            if BLOODLUST_ENABLED and getattr(flow, 'bloodlust_until', 0) <= current_time and len(FOODS) > 0 and MY_X != 0 and MY_Y != 0:
                for f_id, (fx, fy, sz) in FOODS.items():
                    if sz == 9999:
                        my_dist_sq = (fx - MY_X)**2 + (fy - MY_Y)**2
                        
                        # Only activate if within ~1000 units (roughly screen size)
                        if my_dist_sq > 1000000:
                            continue
                            
                        is_closest = True
                        for sid, (ex, ey) in ENEMY_HEADS.items():
                            edist_sq = (fx - ex)**2 + (fy - ey)**2
                            if edist_sq < my_dist_sq:
                                is_closest = False
                                break
                        if not is_closest:
                            continue
                            
                        path_blocked = False
                        for hx, hy, sid in HITBOXES:
                            dist = point_to_segment_dist(hx, hy, MY_X, MY_Y, fx, fy)
                            if dist < 60:
                                path_blocked = True
                                break
                                
                        if not path_blocked:
                            if my_dist_sq < best_bloodlust_dist:
                                best_bloodlust_dist = my_dist_sq
                                best_bloodlust_food = (fx, fy, f_id)
                                
            if best_bloodlust_food is not None:
                if getattr(flow, 'bloodlust_until', 0) <= current_time:
                    print(f"[{MY_SCORE_STR}] 🩸 BLOODLUST: Running to secure dead snake!")
                    flow.bloodlust_until = current_time + 3.0
                    flow.bloodlust_food_id = best_bloodlust_food[2]
                
                target_x, target_y = best_bloodlust_food[0], best_bloodlust_food[1]
                target_food_id = best_bloodlust_food[2]
                best_food_id = best_bloodlust_food[2]
                
            elif len(FOODS) > 0 and MY_X != 0 and MY_Y != 0:
                # 1. Build a fast O(N) grid-based density map
                grid_size = 150 # 150 units per grid cell
                density_map = {}
                for fx, fy, sz in FOODS.values():
                    gx, gy = int(fx // grid_size), int(fy // grid_size)
                    density_map[(gx, gy)] = density_map.get((gx, gy), 0) + 1
                
                best_score = float('inf')
                best_food_id = None
                
                # Clean up expired ignored zones
                ignored_zones = [z for z in ignored_zones if z[2] > current_time]
                
                # 2. Evaluate all valid food pieces
                for f_id, (fx, fy, sz) in FOODS.items():
                    # Check if this food is inside any of our banned zones (200 units squared = 40000)
                    is_ignored = False
                    for zx, zy, z_exp in ignored_zones:
                        if (fx - zx)**2 + (fy - zy)**2 < 40000:
                            is_ignored = True
                            break
                    if is_ignored:
                        continue
                        
                    dist_sq = (fx - MY_X)**2 + (fy - MY_Y)**2
                    
                    # Calculate local density (3x3 grid around the food)
                    gx, gy = int(fx // grid_size), int(fy // grid_size)
                    density = sum(density_map.get((gx + dx, gy + dy), 0) for dx in [-1, 0, 1] for dy in [-1, 0, 1])
                    
                    if sz == 9999:
                        min_enemy_dist_sq = float('inf')
                        for hx, hy, sid in HITBOXES:
                            edist_sq = (hx - fx)**2 + (hy - fy)**2
                            if edist_sq < min_enemy_dist_sq:
                                min_enemy_dist_sq = edist_sq
                                
                        if dist_sq < min_enemy_dist_sq and (MY_SCORE > 100 or getattr(flow, 'was_chasing_orb', False)):
                            score = -999999999 + dist_sq
                        else:
                            continue
                    else:
                        # Normal food size is ~15. Big dead snake food is 45+.
                        # The user requested that big food should be preferred even if it's 10x further away.
                        # Since score uses dist_sq, distance 10x = dist_sq 100x.
                        # If sz=45 (size_multiplier=3), we need 3^P = ~100. So P = ~4.5.
                        size_multiplier = max(1.0, sz / 15.0)
                        
                        # The score minimizes distance and maximizes density/size
                        score = (dist_sq / (size_multiplier**4.5)) / (density**1.5 + 1)
                    
                    if score < best_score:
                        best_score = score
                        target_x = fx
                        target_y = fy
                        best_food_id = f_id
                        
                if best_food_id is not None:
                    dist_to_food = math.sqrt((target_x - MY_X)**2 + (target_y - MY_Y)**2)
                    
                    # If we are physically on top of the food, it must be a ghost food! Delete it!
                    if dist_to_food < 40:
                        if best_food_id in FOODS:
                            del FOODS[best_food_id]
                        target_food_id = None
                        target_x, target_y = None, None
                        continue
                        
                    # Check if we're stuck targeting the same food
                    if target_food_id == best_food_id:
                        if getattr(flow, 'bloodlust_until', 0) > current_time:
                            # Do not ban the region if we are in bloodlust mode
                            target_food_start_time = current_time
                        else:
                            # Timer is 2 seconds for close food, and grows for far food (e.g. 5 seconds for 500 units away)
                            allowed_time = max(2.0, dist_to_food / 100.0)
                            if current_time - target_food_start_time > allowed_time:
                                # We're stuck! Ban this region for 5 seconds!
                                ignored_zones.append((target_x, target_y, current_time + 5.0))
                                target_food_id = None
                                target_x, target_y = None, None
                    else:
                        target_food_id = best_food_id
                        target_food_start_time = current_time
                        
                    flow.was_chasing_orb = (FOODS.get(target_food_id, (0, 0, 0))[2] == 9999)
                        
                else:
                    flow.was_chasing_orb = False
                        
                    # --- PCA LINE DETECTION (EAT FOOD IN A SWOOP) ---
                    # If this food is part of a large line (like a dead snake), aim for the closest end of the line!
                    if target_x is not None and target_y is not None and FOODS[best_food_id][2] != 9999:
                        nearby = []
                        for f_id, (fx, fy, fsz) in FOODS.items():
                            if (fx - target_x)**2 + (fy - target_y)**2 < 22500: # 150 radius
                                nearby.append((fx, fy))
                                
                        if len(nearby) >= 5:
                            cx = sum(f[0] for f in nearby) / len(nearby)
                            cy = sum(f[1] for f in nearby) / len(nearby)
                            
                            ixx = sum((f[0] - cx)**2 for f in nearby)
                            iyy = sum((f[1] - cy)**2 for f in nearby)
                            ixy = sum((f[0] - cx)*(f[1] - cy) for f in nearby)
                            
                            if ixx != iyy or ixy != 0:
                                alpha = 0.5 * math.atan2(2 * ixy, ixx - iyy)
                                ux = math.cos(alpha)
                                uy = math.sin(alpha)
                                
                                min_p = float('inf')
                                max_p = float('-inf')
                                
                                for fx, fy in nearby:
                                    p = (fx - cx)*ux + (fy - cy)*uy
                                    if p < min_p: min_p = p
                                    if p > max_p: max_p = p
                                    
                                e1x = cx + min_p * ux
                                e1y = cy + min_p * uy
                                e2x = cx + max_p * ux
                                e2y = cy + max_p * uy
                                
                                dist1 = (e1x - MY_X)**2 + (e1y - MY_Y)**2
                                dist2 = (e2x - MY_X)**2 + (e2y - MY_Y)**2
                                
                                if dist1 < dist2:
                                    target_x, target_y = e1x, e1y
                                else:
                                    target_x, target_y = e2x, e2y
                        
                # Calculate the angle towards that food with enemy repulsion
                if target_x is not None and target_y is not None:
                    # Base vector towards food
                    vx = target_x - MY_X
                    vy = target_y - MY_Y
                    
                    # Normalize food vector to length 1.0
                    v_len = math.sqrt(vx**2 + vy**2)
                    if v_len > 0:
                        vx /= v_len
                        vy /= v_len
                else:
                    # NO FOOD: Base vector is our last valid direction
                    last_ang = getattr(flow, 'ai_wander_angle', random.uniform(0, 2 * math.pi))
                    vx = math.cos(last_ang)
                    vy = math.sin(last_ang)
                    
                # Store the base vector so we can replace it later if circled
                base_vx = vx
                base_vy = vy
                    
                # Add repulsion from enemies using PERFECT hitboxes from JS
                max_repulsion = 0
                primary_threat_id = None
                best_rx, best_ry = 0, 0
                
                snake_angles = {} # sid -> list of angles
                
                # Dynamic radius: bigger player = bigger warning/circling radius
                warning_radius = 500.0 + (MY_SCORE / 10.0)
                warning_radius_sq = warning_radius ** 2
                
                for (ex, ey, sid) in HITBOXES:
                    edist_sq = (ex - MY_X)**2 + (ey - MY_Y)**2
                    
                    # Circle Detection logic
                    if edist_sq < warning_radius_sq:
                        ang = math.atan2(ey - MY_Y, ex - MY_X)
                        if sid not in snake_angles:
                            snake_angles[sid] = []
                        snake_angles[sid].append(ang)
                        
                    if edist_sq < warning_radius_sq and getattr(flow, 'bloodlust_until', 0) <= current_time:
                        edist = math.sqrt(edist_sq)
                        if edist == 0: edist = 1 # prevent div by zero
                        
                        # Vector pointing AWAY from the body segment
                        rx = MY_X - ex
                        ry = MY_Y - ey
                        
                        rx /= edist
                        ry /= edist
                        
                        # Repulsion strength grows exponentially stronger the closer we are
                        repulsion = 50000.0 / max(1.0, edist_sq)
                        
                        if repulsion > max_repulsion:
                            max_repulsion = repulsion
                            primary_threat_id = sid
                            best_rx = rx
                            best_ry = ry
                            
                        vx += rx * repulsion
                        vy += ry * repulsion
                        
                # Get the set of currently circling snakes
                circling_sids = getattr(flow, 'circling_sids', set())
                new_circling_sids = set()
                
                for sid, angles in snake_angles.items():
                    if len(angles) < 5: # Needs a few hitboxes to form a circle
                        continue
                        
                    angles.sort()
                    max_gap = 0
                    max_gap_idx = 0
                    for i in range(len(angles)):
                        gap = angles[i] - angles[i-1]
                        if gap < 0:
                            gap += 2 * math.pi
                        if gap > max_gap:
                            max_gap = gap
                            max_gap_idx = i
                            
                    # If the largest gap (escape route) is <= 180 degrees (pi), the snake covers >= 180 degrees
                    if max_gap <= math.pi:
                        new_circling_sids.add(sid)
                        flow.escape_angle = angles[max_gap_idx - 1] + max_gap / 2
                        
                # Check for newly circling snakes
                for sid in new_circling_sids - circling_sids:
                    threat_name = SNAKE_NAMES.get(sid, "")
                    if not threat_name.strip(): threat_name = "NO NAME"
                    print(f"🚨 WARNING: {threat_name} is about to circle you!")
                    
                # Check for snakes that stopped circling us
                for sid in circling_sids - new_circling_sids:
                    threat_name = SNAKE_NAMES.get(sid, "")
                    if not threat_name.strip(): threat_name = "NO NAME"
                    print(f"[{MY_SCORE_STR}] ✅ {threat_name} stopped circling.")
                
                flow.circling_sids = new_circling_sids
                
                # Check for Claustraphobia (multiple snakes squeezing from opposite sides)
                all_angles = []
                for angles in snake_angles.values():
                    all_angles.extend(angles)
                    
                is_claustrophobic = False
                if len(all_angles) >= 10:
                    all_angles.sort()
                    global_max_gap = 0
                    for i in range(len(all_angles)):
                        gap = all_angles[i] - all_angles[i-1]
                        if gap < 0:
                            gap += 2 * math.pi
                        if gap > global_max_gap:
                            global_max_gap = gap
                            
                    if global_max_gap <= math.pi:
                        is_claustrophobic = True
                        
                was_claustrophobic = getattr(flow, 'is_claustrophobic', False)
                if is_claustrophobic and not was_claustrophobic:
                    print(f"[{MY_SCORE_STR}] 😱 Claustraphobia mode")
                flow.is_claustrophobic = is_claustrophobic
                
                # Override movement to BREAK FREE if circled
                if new_circling_sids and hasattr(flow, 'escape_angle'):
                    vx = math.cos(flow.escape_angle)
                    vy = math.sin(flow.escape_angle)
                elif getattr(flow, 'evade_until', 0) > current_time and not is_claustrophobic and getattr(flow, 'bloodlust_until', 0) <= current_time:
                    if hasattr(flow, 'locked_evade_angle'):
                        vx = math.cos(flow.locked_evade_angle)
                        vy = math.sin(flow.locked_evade_angle)
                        
                # atan2 gives angle from vectors
                ang = math.atan2(vy, vx)
                
                # Save wandering direction in case we lose food
                flow.ai_wander_angle = ang
                
                # Slither.io expects angle from 0 to 2*pi
                if ang < 0:
                    ang += 2 * math.pi
                
                # Convert to byte (0 to 250)
                base_angle = int((ang / (2 * math.pi)) * 251)
                
                # Add some random jitter to simulate human imprecision (-3 to +3)
                if new_circling_sids and hasattr(flow, 'escape_angle'):
                    jitter = 0
                else:
                    jitter = random.randint(-3, 3)
                auto_angle = (base_angle + jitter) % 251
                
                if getattr(flow, 'bloodlust_until', 0) > current_time:
                    new_state = "BLOODLUST"
                elif new_circling_sids and hasattr(flow, 'escape_angle'):
                    new_state = "BREAK_FREE"
                elif target_x is not None and target_y is not None:
                    # Calculate if we are deviating from the pure food path
                    food_angle = math.atan2(target_y - MY_Y, target_x - MY_X)
                    if food_angle < 0: food_angle += 2 * math.pi
                    
                    diff = abs(ang - food_angle)
                    if diff > math.pi: diff = 2 * math.pi - diff
                    
                    if diff > 0.15:
                        new_state = "EVADING"
                    else:
                        new_state = "SEEKING"
                else:
                    new_state = "NO_FOOD"
                    
                if new_state != current_ai_state:
                    if new_state == "EVADING":
                        flow.evade_until = current_time + 1.0
                        if primary_threat_id is not None:
                            flow.locked_evade_angle = math.atan2(best_ry, best_rx)
                        else:
                            flow.locked_evade_angle = ang
                            
                    if new_state == "BLOODLUST":
                        pass # Message handled at trigger
                    elif new_state == "BREAK_FREE":
                        state_msg = "🚨 BREAKING FREE: Racing towards the escape gap!"
                    elif new_state == "SEEKING":
                        state_msg = "🎯 Heading directly towards food!"
                    elif new_state == "EVADING":
                        threat_name = SNAKE_NAMES.get(primary_threat_id, "") if primary_threat_id is not None else ""
                        if not threat_name.strip(): threat_name = "NO NAME"
                        state_msg = f"⚠️ Swerving to avoid snake: {threat_name}!"
                    else:
                        state_msg = "👉 Not heading towards food: No valid food pieces found nearby! Wandering safely."
                    print(f"[{MY_SCORE_STR}] {state_msg}")
                    current_ai_state = new_state
            
            # Handle Dash / Boosting
            is_boosting = getattr(flow, 'ai_is_boosting', False)
            targeting_prey = target_food_id is not None and target_food_id in FOODS and FOODS[target_food_id][2] == 9999
            
            food_is_far = False
            if target_x is not None and target_y is not None and current_ai_state == "SEEKING":
                dist_sq = (target_x - MY_X)**2 + (target_y - MY_Y)**2
                if dist_sq > 40000: # 200 units
                    food_is_far = True
                    
            should_boost = (current_ai_state == "BREAK_FREE") or (current_ai_state == "BLOODLUST") or targeting_prey or food_is_far
            
            if should_boost and not is_boosting:
                flow.ai_is_boosting = True
                try:
                    flow.ai_last_injected_boost = 253
                    ctx.master.commands.call("inject.websocket", flow, False, bytes([253]), False)
                    print(f"[{MY_SCORE_STR}] 🚀 DASH ACTIVATED!")
                except: pass
            elif not should_boost and is_boosting:
                flow.ai_is_boosting = False
                try:
                    flow.ai_last_injected_boost = 254
                    ctx.master.commands.call("inject.websocket", flow, False, bytes([254]), False)
                    print(f"[{MY_SCORE_STR}] 🛑 DASH DEACTIVATED.")
                except: pass
                
            # Save the angle so our websocket_message hook knows not to drop it
            flow.ai_last_injected_angle = auto_angle
            
            try:
                # Inject the websocket frame directly to the server
                ctx.master.commands.call("inject.websocket", flow, False, bytes([auto_angle]), False)
            except Exception as e:
                print(f"[AI] Stopped injecting due to error: {e}")
                break
                
            # Randomize packet timing between 80ms and 150ms to prevent bot detection
            await asyncio.sleep(random.uniform(0.08, 0.15))
    except asyncio.CancelledError:
        pass
    print("[AI] Background task ended.")

def process_server_packet(flow, data):
    """Processes server-to-client messages to track snake position and food."""
    global MY_SNAKE_ID, MY_X, MY_Y, MSL
    global FOODS, sector_size, ssd256
    
    if not data:
        return
        
    cmd = chr(data[0])
    
    # 0. Game setup (get sector_size)
    if cmd == 'a':
        m = 1
        m += 3 # skip grd
        m += 2 # skip nmscps
        if m + 1 < len(data):
            sector_size = (data[m] << 8) | data[m+1]
            ssd256 = sector_size / 256.0
            print(f"[*] Map configuration loaded: Sector Size = {sector_size}, ssd256 = {ssd256:.3f}")

    # 4. Death tracking
    if cmd == 'v':
        global MY_MAX_SCORE, MY_SCORE, MY_SCORE_STR, MY_X, MY_Y, MY_SNAKE_ID
        global HITBOXES, FOODS, SNAKE_NAMES
        
        score = MY_MAX_SCORE
        high_score = get_high_score()
        
        print(f"\n=====================================")
        print(f"💀 AI DIED! Final Score: {score}")
        if score > high_score:
            set_high_score(score)
            print(f"🎉 NEW HIGH SCORE: {score} 🎉")
        else:
            print(f"High Score Remains: {high_score}")
        print(f"=====================================\n")
        
        # Reset all global variables for the next game
        MY_MAX_SCORE = 0
        MY_SCORE = 0
        MY_SCORE_STR = "0"
        MY_X = 0
        MY_Y = 0
        MY_SNAKE_ID = None
        HITBOXES.clear()
        FOODS.clear()
        SNAKE_NAMES.clear()
        
        # Terminate the AI loop and clear circle state
        if hasattr(flow, 'has_spawned'):
            flow.has_spawned = False
        if hasattr(flow, 'circling_sids'):
            flow.circling_sids = set()
        if hasattr(flow, 'ai_is_boosting'):
            flow.ai_is_boosting = False

    # 1. Spawn tracking
    if cmd == 's':
        if len(data) > 7 and not getattr(flow, 'has_spawned', False):
            MY_SNAKE_ID = (data[1] << 8) | data[2]
            flow.has_spawned = True
            FOODS.clear() # Clear old food
            print(f"[*] Locked Snake ID: {MY_SNAKE_ID}. AI takes control!")
            flow.ai_task = asyncio.create_task(ai_loop(flow))

    # 2. Track absolute position of MY SNAKE
    if cmd in ['=', '+']:
        # My snake
        if len(data) in [7, 10]:
            MY_X = (data[3] << 8) | data[4]
            MY_Y = (data[5] << 8) | data[6]

    # 3. Track relative position
    if cmd in ['G', 'N']:
        if len(data) in [3, 6]:
            iang = (data[1] << 8) | data[2]
            ang = iang * (2 * math.pi / 65536)
            MY_X += math.cos(ang) * MSL
            MY_Y += math.sin(ang) * MSL

def websocket_message(flow: http.HTTPFlow) -> None:
    if not hasattr(flow, 'has_spawned'):
        flow.has_spawned = False

    if flow.websocket is not None:
        message = flow.websocket.messages[-1]
        
        # Intercept custom JS Telemetry from client
        if message.from_client:
            if message.content and len(message.content) > 2:
                if message.content[0] == 240:
                    # Snake Identity mapping
                    message.drop()
                    if len(message.content) > 3:
                        sid = (message.content[1] << 8) | message.content[2]
                        try:
                            nk = message.content[3:].decode('utf-8', errors='ignore')
                            SNAKE_NAMES[sid] = nk
                        except:
                            pass
                    return
                    
                if message.content[0] == 241:
                    # PERFECT FOOD TELEMETRY
                    message.drop()
                    global FOODS
                    FOODS.clear()
                    
                    # fbuf[1:] contains triplets of uint16 (x, y, sz)
                    f_idx = 0
                    for i in range(1, len(message.content), 6):
                        if i + 5 < len(message.content):
                            x = (message.content[i] << 8) | message.content[i+1]
                            y = (message.content[i+2] << 8) | message.content[i+3]
                            sz = (message.content[i+4] << 8) | message.content[i+5]
                            FOODS[f_idx] = (x, y, sz)
                            f_idx += 1
                    return
                    
                if message.content[0] == 242 and len(message.content) >= 5:
                    # Drop the packet so the real server doesn't ban us
                    message.drop()
                    
                    # Update current score
                    global MY_SCORE, MY_MAX_SCORE, MY_SCORE_STR
                    MY_SCORE = (message.content[1] << 24) | (message.content[2] << 16) | (message.content[3] << 8) | message.content[4]
                if MY_SCORE > MY_MAX_SCORE:
                    MY_MAX_SCORE = MY_SCORE
                MY_SCORE_STR = str(MY_SCORE)
                
                # PERFECT HITBOXES BY SNAKE
                global HITBOXES, ENEMY_HEADS
                HITBOXES.clear()
                ENEMY_HEADS.clear()
                
                idx = 5
                while idx + 3 < len(message.content):
                    sid = (message.content[idx] << 8) | message.content[idx+1]
                    num_pts = (message.content[idx+2] << 8) | message.content[idx+3]
                    idx += 4
                    
                    for i in range(num_pts):
                        if idx + 3 < len(message.content):
                            hx = (message.content[idx] << 8) | message.content[idx+1]
                            hy = (message.content[idx+2] << 8) | message.content[idx+3]
                            idx += 4
                            HITBOXES.append((hx, hy, sid))
                            if i == 0:
                                ENEMY_HEADS[sid] = (hx, hy)
                        else:
                            break
                return
        
        # Parse Server packets to update our state (food & snake positions)
        if not message.from_client:
            data = message.content
            if len(data) < 2: return
            
            is_multiplexed = (data[0] < 32)
            if is_multiplexed:
                m = 0
                while m < len(data):
                    if m + 1 >= len(data): break
                    if data[m] < 32:
                        length = (data[m] << 8) | data[m+1]
                        hlen = 2
                    else:
                        length = data[m] - 32
                        hlen = 1
                    
                    if m + hlen + length > len(data): break
                    packet = data[m+hlen:m+hlen+length]
                    process_server_packet(flow, packet)
                    m += hlen + length
            else:
                process_server_packet(flow, data)
            return

        # Intercept Client -> Server packets
        if message.from_client:
            client_data = message.content
            if len(client_data) > 0:
                cmd = client_data[0]
                
                # 'Play' request (115) with nickname triggers game start
                if cmd == 115 and len(client_data) > 1:
                    flow.has_spawned = False
                    print("[*] Play request sent. Waiting for server to spawn us...")
                
                # If AI is active, block ALL user input packets EXCEPT our own injected packets
                if getattr(flow, 'has_spawned', False) and len(client_data) == 1:
                    if cmd <= 250 and cmd != 112:
                        if getattr(flow, 'ai_last_injected_angle', -1) == cmd:
                            flow.ai_last_injected_angle = -1 # Clear and let our injected packet pass
                            return
                        message.drop() # Drop the user's manual steering
                    
                    elif cmd in [253, 254]:
                        if getattr(flow, 'ai_last_injected_boost', -1) == cmd:
                            flow.ai_last_injected_boost = -1 # Clear and let our injected packet pass
                            return
                        message.drop() # Drop manual boosting
