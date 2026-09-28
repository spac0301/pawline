"""Recognising a pet being stroked.

Kept apart from the overlay, and stdlib-only, so the gesture can be tested
without a display -- which matters here more than most places, because the
whole difficulty is a judgement call about intent. Nothing distinguishes
"stroking the pet" from "moving the pointer across it" except that a stroke
turns around, and a rule about that is only as good as the cases it is tried
against.

Direction is what counts, not distance. Movements too small to be deliberate
are discarded, so a hand resting on a mouse never accumulates into affection,
and reversals expire, so two turns a minute apart are not one gesture.
"""

from __future__ import annotations

import math

#: How far the pointer must travel before a movement counts as a stroke at all,
#: rather than the hand shaking on a stationary mouse.
STROKE_PIXELS = 8

#: How far the pointer's direction must turn, in total, to count as a
#: gesture. Radians.
#:
#: Counting reversals -- turns of more than a right angle -- worked for
#: rubbing back and forth and could never work for a circle, which turns
#: gradually and reverses never. Accumulated turning covers both: half a
#: turn per reversal of a rub, a full turn per loop of a circle. Set a
#: little above one circle, so a small deliberate loop is enough and the
#: ordinary curve of a pointer on its way somewhere is not.
TURN_RADIANS = 2.2 * math.pi

#: What a summons asks for: about four fifths of a circle. Less than a rub,
#: because the gesture is bigger and therefore slower.
CALL_TURN_RADIANS = 1.6 * math.pi

#: Past this, a change of direction is a reversal rather than a curve.
#: Three quarters of a half-turn: enough room for a hand drawing a circle
#: in coarse steps, far from the doubling back of a wave.
REVERSAL_RADIANS = 0.75 * math.pi

#: How big the gesture has to be, measured across whatever it drew.
#:
#: Turning alone does not say how large a thing was drawn, and a tiny
#: twirl of the wrist accumulates a full turn in no distance at all -- so
#: the pet came for gestures far smaller than anyone meant to make. About
#: a coin across, which is small enough to be no effort and large enough
#: to be deliberate.
CALL_SPAN_PIXELS = 90

#: How round it has to be: the width of what was drawn across its narrow
#: way, as a fraction of its width across the long way.
#:
#: Measured through the shape's own axes rather than the screen's. Comparing
#: the bounding box did the job for a loop lying flat, and none at all for
#: one lying at an angle: a 400x100 ellipse turned 45 degrees has a bounding
#: box of 354x354, which is a perfect square, so the thinnest sliver drawn
#: cornerwise scored full marks. The axes come out of the second moments of
#: the points, which have no opinion about which way up the screen is.
#:
#: Nobody draws a true circle freehand, so there is room here, but not much:
#: at three fifths a 300x180 oval still counts and a 300x150 one does not,
#: whichever way round either is turned.
CALL_ROUNDNESS = 0.6

#: How long the whole gesture may take, from the first turn to the last.
#:
#: There was no limit at all, only a limit on the gap between samples, so a
#: circle drawn at a leisurely pace over several seconds counted exactly as
#: a quick one did -- and the pointer wanders round the screen slowly all
#: day. Drawing a circle *deliberately* is a quick thing; this is what
#: separates it from having merely gone that way eventually.
#:
#: The threshold is four fifths of a turn, so this works out at half a
#: second for the full circle. Set at a second and a half first and reported
#: as far too loose. It follows that a large circle is no longer a summons
#: at all -- a hand cannot carry a 400px radius round in half a second --
#: which is the intended shape of the rule, not a side effect of it: what is
#: being asked for is a quick flick of a circle, not a slow sweep.
CALL_SECONDS = 0.4

#: Reversals only belong to the same gesture within this long of each other.
WINDOW_SECONDS = 1.6

#: A pet reacting to every stroke of a long rub is a pet having a seizure.
COOLDOWN_SECONDS = 2.5


class Stroke:
    """Tracks a hovering pointer and says when it has been stroking.

    `feed` is given the pointer's x and the time, and returns True on the
    moment the gesture completes -- once per rub, not once per wobble.
    """

    def __init__(
        self,
        turn_radians: float = TURN_RADIANS,
        span_pixels: int = 0,
        one_way: bool = False,
        seconds: float = 0.0,
        roundness: float = CALL_ROUNDNESS,
    ) -> None:
        #: Stroking and summoning want different amounts of it. A rub gives
        #: half a turn per sweep and happens on a sprite barely a hundred
        #: pixels wide, so it can ask for a lot; a summons is often a circle
        #: drawn at arm's length, and a big circle at an ordinary hand speed
        #: takes seconds per revolution -- ask for more than one and a
        #: normal-sized circle never finishes in time.
        self.turn_radians = turn_radians
        #: How far the gesture must reach across, if at all. Stroking a
        #: sprite is confined to the sprite and cannot ask for much; a
        #: summons drawn anywhere on screen can.
        self.span_pixels = span_pixels
        #: Whether the turning has to keep going the same way round.
        #:
        #: A circle does; a wave does not, its turns cancelling out. Adding
        #: up the sizes of the turns cannot tell them apart, so waving the
        #: pointer about while working summoned the pet. Keeping the sign
        #: separates them exactly: two loops of a circle come to +3.9pi,
        #: the same length of wave to 0.
        self.one_way = one_way
        #: How long the whole gesture may take; 0 for no limit.
        #:
        #: A rub needs none -- it happens on the sprite, where nothing else
        #: is going on. A summons is drawn out in the open, where the
        #: pointer travels all day, and without this the only thing
        #: separating "drew a circle" from "went round eventually" was that
        #: no single pause exceeded the sample window.
        self.seconds = seconds
        #: How round it has to be. An argument rather than the constant, so the
        #: person it is wrong for can move it without editing the source.
        self.roundness_wanted = roundness
        self.began = 0.0
        self.low_x = self.high_x = 0
        self.low_y = self.high_y = 0
        self.x: int | None = None
        self.y = 0
        self.at = 0.0
        self.direction_x = 0.0
        self.direction_y = 0.0
        self.turned = 0.0
        self.turned_signed = 0.0
        self.ready_at = 0.0
        self._forget_shape()

    def _forget_shape(self) -> None:
        """Start measuring the shape again from nothing."""
        self.count = 0
        self.sum_x = self.sum_y = 0.0
        self.sum_xx = self.sum_yy = self.sum_xy = 0.0

    def _remember_shape(self, x: int, y: int) -> None:
        self.count += 1
        self.sum_x += x
        self.sum_y += y
        self.sum_xx += float(x) * x
        self.sum_yy += float(y) * y
        self.sum_xy += float(x) * y

    def roundness(self) -> float:
        """How round what has been drawn is, from 0 (a line) to 1 (a circle).

        The two second moments of the points along their own principal axes,
        the shorter over the longer. Rotating the shape rotates the axes with
        it and leaves the answer alone, which is the whole point: the
        bounding box this replaced called a sliver drawn cornerwise a perfect
        circle.
        """
        if self.count < 4:
            return 0.0
        count = self.count
        mean_x, mean_y = self.sum_x / count, self.sum_y / count
        var_x = self.sum_xx / count - mean_x * mean_x
        var_y = self.sum_yy / count - mean_y * mean_y
        covar = self.sum_xy / count - mean_x * mean_y
        spread = math.sqrt(max(0.0, (var_x - var_y) ** 2 + 4 * covar * covar))
        major = (var_x + var_y + spread) / 2
        minor = (var_x + var_y - spread) / 2
        if major <= 0:
            return 0.0
        return math.sqrt(max(0.0, minor) / major)

    def reset(self) -> None:
        """Forget the gesture in progress, when the pointer leaves or drags."""
        self.x = None
        self.began = 0.0
        self._forget_shape()
        self.direction_x = 0.0
        self.direction_y = 0.0
        self.turned = self.turned_signed = 0.0
        self.low_x = self.high_x = 0
        self.low_y = self.high_y = 0

    def feed(self, x: int, now: float, y: int = 0) -> bool:
        """Feed a pointer position. `y` is optional; without it, x alone decides.

        Direction is a vector, not a sign, so a gesture counts however it is
        oriented. Watching only x meant waving up and down at the pet did
        nothing at all -- which is not a thing anyone would guess was
        deliberate, and several people wave that way.
        """
        if self.x is None:
            self.x, self.y, self.at = x, y, now
            self.began = now
            self.low_x = self.high_x = x
            self.low_y = self.high_y = y
            self._forget_shape()
            self._remember_shape(x, y)
            return False

        travelled_x = x - self.x
        travelled_y = y - self.y
        if math.hypot(travelled_x, travelled_y) < STROKE_PIXELS:
            # Too small to mean anything. Deliberately does not update the
            # anchor: a slow drag across the sprite would otherwise never
            # accumulate enough travel to register at all.
            return False

        too_slow = bool(self.seconds) and now - self.began > self.seconds
        if now - self.at > WINDOW_SECONDS or too_slow:
            # Whatever came before was a different gesture -- either too long
            # ago to belong to this one, or too long in the drawing to have
            # been one. Start again from here rather than discarding the
            # sample: the circle someone is drawing right now may well be the
            # deliberate one.
            self.turned = self.turned_signed = 0.0
            self.low_x = self.high_x = x
            self.low_y = self.high_y = y
            self.began = now
            self._forget_shape()
        self.low_x, self.high_x = min(self.low_x, x), max(self.high_x, x)
        self.low_y, self.high_y = min(self.low_y, y), max(self.high_y, y)
        self._remember_shape(x, y)
        if self.direction_x or self.direction_y:
            # The angle between one movement and the next, added up. A rub
            # turns half a circle every time it goes back; a circle turns a
            # whole one each loop; a pointer crossing the sprite on its way
            # somewhere turns almost nothing.
            angle = math.atan2(
                self.direction_x * travelled_y - self.direction_y * travelled_x,
                self.direction_x * travelled_x + self.direction_y * travelled_y,
            )
            self.turned += abs(angle)
            # A turn of nearly half a circle is a reversal, not a rotation,
            # and must not count towards going round. It also has no
            # reliable sign: reverse exactly and the cross product is zero,
            # so atan2 answers +pi every time and a plain back-and-forth
            # accumulates in one direction exactly as a circle would.
            if abs(angle) < REVERSAL_RADIANS:
                self.turned_signed += angle
        self.direction_x, self.direction_y = float(travelled_x), float(travelled_y)
        self.x, self.y, self.at = x, y, now

        span = max(self.high_x - self.low_x, self.high_y - self.low_y)
        turning = abs(self.turned_signed) if self.one_way else self.turned
        round_enough = not self.span_pixels or self.roundness() >= self.roundness_wanted
        if (
            turning < self.turn_radians
            or span < self.span_pixels
            or not round_enough
            or now < self.ready_at
        ):
            return False
        self.turned = self.turned_signed = 0.0
        self.low_x = self.high_x = x
        self.low_y = self.high_y = y
        self.began = now
        self._forget_shape()
        self._remember_shape(x, y)
        self.ready_at = now + COOLDOWN_SECONDS
        return True


#: Past this, a bend is the corner of a drawn shape rather than the curve of
#: a hand going round. A star turns 144 degrees at every point, a square 90 --
#: so this sits between them, near enough the square that a sloppy star still
#: lands and far enough that a neat square does not.
#:
#: Accumulated over consecutive samples rather than measured on one. Measured
#: on one it missed corners outright: a sample landing on the point splits its
#: 144 degrees into two turns of 72, neither of which is a corner, and five
#: points sampled every 50ms give that several chances to happen. Reported as
#: the gesture simply not working.
CORNER_RADIANS = 0.6 * math.pi

#: Under this a step is going straight on, and whatever corner was being
#: turned is over.
#:
#: This is also what separates a star from a circle, and the separation has to
#: be structural rather than a matter of degree: a circle sampled coarsely
#: turns 40 degrees a step and would accumulate a corner's worth every few
#: samples. So a corner only counts if the pointer was travelling straight
#: before it -- which a star does along every edge, and a circle never does.
STRAIGHT_RADIANS = 0.16 * math.pi

#: How many corners make a star. Five points means five corners; four is
#: asked for so that one lost to a coarse sample does not lose the gesture.
#: A triangle cannot reach it whatever its angles, which is the point.
STAR_CORNERS = 4

#: How big, across, and how long it may take. A star is more drawing than a
#: circle is, so it gets longer -- but not all day, or any scribble
#: eventually qualifies.
#:
#: Well over twice the circle's minimum, and raised from 120 after small ones
#: were going off. Teleporting is the more disruptive of the two answers -- the
#: pet is simply somewhere else -- so it is worth asking for something nobody
#: draws by accident. Adjustable in the tuning window either way.
STAR_SPAN_PIXELS = 220
STAR_SECONDS = 2.5


class Star:
    """Recognises a star drawn in one stroke, for teleporting the pet to it.

    A star is corners with straight edges between them; a circle is a curve.
    That is the whole of how they are told apart. The pentagram {5/2} turns
    144 degrees at each of its five points and always the same way round, so
    the corners are counted, their direction has to agree, and each has to sit
    between two straight runs.

    Kept apart from `Stroke` rather than folded into it. Stroke fires on how
    far the direction has turned in total, which is the right question for a
    circle and says nothing useful about a star -- the same five corners come
    to 720 degrees whether they are arranged as a star or as a scribble.
    """

    def __init__(
        self,
        corners: int = STAR_CORNERS,
        span_pixels: int = STAR_SPAN_PIXELS,
        seconds: float = STAR_SECONDS,
    ) -> None:
        self.corners_wanted = corners
        self.span_pixels = span_pixels
        self.seconds = seconds
        self.x: int | None = None
        self.y = 0
        self.at = 0.0
        self.began = 0.0
        self.direction_x = 0.0
        self.direction_y = 0.0
        self.corners = 0
        self.turn_sign = 0
        #: The bend being turned right now, added up over however many samples
        #: it takes, and whether the pointer went straight before it.
        self.bend = 0.0
        self.went_straight = False
        self.low_x = self.high_x = 0
        self.low_y = self.high_y = 0
        self.ready_at = 0.0

    def reset(self) -> None:
        self.x = None
        self.corners = 0
        self.turn_sign = 0
        self.bend = 0.0
        self.went_straight = False
        self.direction_x = self.direction_y = 0.0

    def _restart(self, x: int, y: int, now: float) -> None:
        self.corners = 0
        self.turn_sign = 0
        self.bend = 0.0
        self.went_straight = False
        self.began = now
        self.low_x = self.high_x = x
        self.low_y = self.high_y = y

    def _corner(self, sign: int) -> None:
        """Record a corner, or start again if it turned the other way."""
        if self.turn_sign and sign != self.turn_sign:
            # A corner the other way is a zigzag, not a star. Count from this
            # one rather than discarding it: it may be the first of the star
            # being drawn now.
            self.corners = 1
        else:
            self.corners += 1
        self.turn_sign = sign
        self.bend = 0.0
        self.went_straight = False

    def feed(self, x: int, now: float, y: int = 0) -> bool:
        """Feed a pointer position; True on the moment a star completes."""
        if self.x is None:
            self.x, self.y, self.at = x, y, now
            self._restart(x, y, now)
            return False

        travelled_x, travelled_y = x - self.x, y - self.y
        if math.hypot(travelled_x, travelled_y) < STROKE_PIXELS:
            return False

        if now - self.at > WINDOW_SECONDS or now - self.began > self.seconds:
            self._restart(x, y, now)
        self.low_x, self.high_x = min(self.low_x, x), max(self.high_x, x)
        self.low_y, self.high_y = min(self.low_y, y), max(self.high_y, y)

        if self.direction_x or self.direction_y:
            angle = math.atan2(
                self.direction_x * travelled_y - self.direction_y * travelled_x,
                self.direction_x * travelled_x + self.direction_y * travelled_y,
            )
            if abs(angle) < STRAIGHT_RADIANS:
                self.bend = 0.0
                self.went_straight = True
            else:
                sign = 1 if angle > 0 else -1
                self.bend = angle if self.bend * angle < 0 else self.bend + angle
                if abs(self.bend) >= CORNER_RADIANS and self.went_straight:
                    self._corner(sign)
        self.direction_x, self.direction_y = float(travelled_x), float(travelled_y)
        self.x, self.y, self.at = x, y, now

        span = max(self.high_x - self.low_x, self.high_y - self.low_y)
        if (
            self.corners < self.corners_wanted
            or span < self.span_pixels
            or now < self.ready_at
        ):
            return False
        self._restart(x, y, now)
        self.ready_at = now + COOLDOWN_SECONDS
        return True
