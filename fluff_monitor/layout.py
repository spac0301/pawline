"""Shared geometry policy for the native GTK and Qt frontends."""
def beside_position(bounds, size, area, avoid=(), gap=12):
    """Prefer a clear side of the sprite, then above/below the occupied group."""
    ax, ay, aw, ah = bounds
    width, height = size
    blocked = [bounds, *avoid]
    left = min(r[0] for r in blocked)
    top = min(r[1] for r in blocked)
    right = max(r[0]+r[2] for r in blocked)
    bottom = max(r[1]+r[3] for r in blocked)
    cx, cy = ax + aw/2, ay + ah/2
    candidates = [(ax-width-gap, cy-height/2), (ax+aw+gap, cy-height/2),
                  (cx-width/2, ay-height-gap), (cx-width/2, ay+ah+gap)]
    outside = [(left-width-gap, cy-height/2), (right+gap, cy-height/2),
               (cx-width/2, top-height-gap), (cx-width/2, bottom+gap)]
    candidates += sorted(outside, key=lambda p: (p[0]+width/2-cx)**2 + (p[1]+height/2-cy)**2)
    if width > area.width or height > area.height:
        return None
    for x, y in candidates:
        x = max(area.x, min(round(x), area.x+area.width-width))
        y = max(area.y, min(round(y), area.y+area.height-height))
        if all(x+width+gap <= bx or bx+bw+gap <= x or
               y+height+gap <= by or by+bh+gap <= y for bx, by, bw, bh in blocked):
            return x, y
    return None

