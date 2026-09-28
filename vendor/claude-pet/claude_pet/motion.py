"""Throwing the pet, and it coming when called.

Both are movements of the sprite's *position*, which is the only kind of motion
this project can invent. A sprite pack ships nine fixed rows and none of them
is a fall or a sleep, so anything that needs new art is off the table -- but
where the pet *is* belongs to us, and momentum costs no pixels at all.

Stdlib only and free of GTK, so the arithmetic can be tried against awkward
numbers without a display: a throw at the very edge, one aimed off-screen, one
too gentle to be a throw at all.
"""

from __future__ import annotations

import math

#: Below this, releasing the pet is putting it down rather than throwing it.
#: Pixels per second, measured over the tail of the drag.
#:
#: Set high on purpose, and raised twice after being reported too eager. An
#: ordinary drag peaks well past a thousand, and plenty of people let go
#: while still moving rather than stopping first -- so a low bar turns
#: ordinary placements into throws, which is the worse way to be wrong: it
#: puts the pet somewhere its owner did not.
#:
#: `claude-pet run` with CLAUDE_PET_DEBUG=1 prints the speed of every
#: release, so this can be settled with measurements rather than a third
#: guess.
THROW_SPEED = 4500.0

#: And it must actually cover ground in that window, so that a shake at the
#: moment of release cannot reach the speed threshold while going nowhere.
THROW_TRAVEL = 110.0

#: What fraction of its speed a throw keeps after one second. Low, because a
#: pet that skates about for five seconds stops being funny quickly.
FRICTION = 0.08

#: Speed at which a throw is over and the pet settles.
SETTLE_SPEED = 40.0

#: How much of its speed a bounce off the screen edge gives back. Well under
#: half: a pet ricocheting corner to corner reads as a bug, not as physics.
BOUNCE = 0.45

#: How much of the drag's tail decides the throw. Short enough that only the
#: final flick counts, so dragging somewhere slowly and letting go stays a
#: placement however far the pointer travelled getting there.
FLICK_SECONDS = 0.12


class Throw:
    """A pet in flight: velocity, friction, and the walls it bounces off.

    Friction and bounce are arguments rather than constants because they are
    the two knobs worth handing over -- how far a throw carries and how much
    the walls give back are matters of taste, and nobody can settle theirs
    from a constant in a file they never open. Clamped, since a friction of 1
    is a pet that never stops and 0 is one that never moves.
    """

    def __init__(
        self,
        velocity_x: float,
        velocity_y: float,
        friction: float = FRICTION,
        bounce: float = BOUNCE,
    ) -> None:
        self.velocity_x = velocity_x
        self.velocity_y = velocity_y
        self.friction = min(max(friction, 0.001), 0.9)
        self.bounce = min(max(bounce, 0.0), 0.95)

    @property
    def moving(self) -> bool:
        return abs(self.velocity_x) + abs(self.velocity_y) >= SETTLE_SPEED

    def step(
        self,
        x: float,
        y: float,
        bounds: tuple[int, int, int, int],
        seconds: float,
        clamp=None,
    ) -> tuple[float, float]:
        """Advance by `seconds`, bouncing off the edge of where it may be.

        Time-based rather than per-frame: the animation rate is a user setting,
        and a throw that travelled further on a faster pet would be a strange
        thing to have built.

        `bounds` is the simple case -- one rectangle, which is what a single
        screen or a row of them is. `clamp` is for when the walls are not a
        rectangle: give it a function that pulls a point back to the nearest
        legal spot and the bounce follows those walls instead. Three monitors
        in an L have a corner with no screen behind it, and a rectangle cannot
        describe that, so a throw sailed straight into it.
        """
        decay = self.friction**seconds

        # The exact integral of v·FRICTION^t over the step, rather than
        # velocity times elapsed. Multiplying is a Riemann sum, and its error
        # grows with the step -- which would make a throw travel a different
        # distance on a pet animating at 4fps than at 30, both of which are
        # settings someone can choose.
        travel = (1.0 - decay) / -math.log(self.friction)
        x += self.velocity_x * travel
        y += self.velocity_y * travel

        self.velocity_x *= decay
        self.velocity_y *= decay

        if clamp is not None:
            # Whatever the shape of the allowed area, the wall is wherever the
            # clamp had to move the point. Reflect on each axis it moved on, so
            # the pet bounces off the inside corner of an L exactly as it does
            # off the edge of a screen.
            legal_x, legal_y = clamp(x, y)
            if legal_x != x:
                # Away from the wall it hit, not simply reversed: reversing
                # would send it back out again when the clamp pushed it along.
                self.velocity_x = abs(self.velocity_x) * self.bounce
                if legal_x < x:
                    self.velocity_x = -self.velocity_x
                x = legal_x
            if legal_y != y:
                self.velocity_y = abs(self.velocity_y) * self.bounce
                if legal_y < y:
                    self.velocity_y = -self.velocity_y
                y = legal_y
            return x, y

        left, top, right, bottom = bounds
        if x <= left:
            x, self.velocity_x = left, abs(self.velocity_x) * self.bounce
        elif x >= right:
            x, self.velocity_x = right, -abs(self.velocity_x) * self.bounce
        if y <= top:
            y, self.velocity_y = top, abs(self.velocity_y) * self.bounce
        elif y >= bottom:
            y, self.velocity_y = bottom, -abs(self.velocity_y) * self.bounce

        return x, y


class Flick:
    """The tail end of a drag, which is what decides whether it was a throw.

    Only the last fraction of a second counts. Someone dragging the pet slowly
    across the screen and letting go has placed it, however far it went; the
    speed at the moment of release is the whole question.
    """

    def __init__(
        self,
        threshold: float = THROW_SPEED,
        friction: float = FRICTION,
        bounce: float = BOUNCE,
    ) -> None:
        #: How hard the flick has to be. Handed over for the same reason as
        #: friction: it was argued about twice and guessed at three times, and
        #: the person it is wrong for is the only one who can say so.
        self.threshold = threshold
        self.friction = friction
        self.bounce = bounce
        self.samples: list[tuple[float, float, float]] = []

    def clear(self) -> None:
        self.samples.clear()

    def record(self, x: float, y: float, now: float) -> None:
        self.samples.append((now, x, y))
        cutoff = now - FLICK_SECONDS
        while len(self.samples) > 2 and self.samples[0][0] < cutoff:
            self.samples.pop(0)

    def release(self) -> Throw | None:
        """The throw this drag ended in, or None if it was a placement."""
        if len(self.samples) < 2:
            return None
        (first_at, first_x, first_y), (last_at, last_x, last_y) = (
            self.samples[0],
            self.samples[-1],
        )
        elapsed = last_at - first_at
        if elapsed <= 0:
            return None

        travelled_x, travelled_y = last_x - first_x, last_y - first_y
        if math.hypot(travelled_x, travelled_y) < THROW_TRAVEL:
            return None

        velocity_x = travelled_x / elapsed
        velocity_y = travelled_y / elapsed
        # Straight-line speed, not the sum of the parts: adding them makes a
        # diagonal drag read as half again as fast as it was, so throws
        # aimed at a corner triggered on gentler flicks than sideways ones.
        if math.hypot(velocity_x, velocity_y) < self.threshold:
            return None
        return Throw(velocity_x, velocity_y, self.friction, self.bounce)

    def speed(self) -> float:
        """Straight-line speed at release, in pixels per second.

        Separate from `release` so the number can be reported whatever the
        verdict: a threshold argued about twice is one to settle with
        measurements.
        """
        if len(self.samples) < 2:
            return 0.0
        (first_at, first_x, first_y), (last_at, last_x, last_y) = (
            self.samples[0],
            self.samples[-1],
        )
        elapsed = last_at - first_at
        if elapsed <= 0:
            return 0.0
        return math.hypot(last_x - first_x, last_y - first_y) / elapsed
