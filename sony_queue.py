"""Distinguish a queued track ending from stop, pause, and source changes."""
import random
import time


class QueueFollower:
    def __init__(self):
        self.previous = None
        self.playlist = None
        self.visited = set()
        self.suppressed = False

    def cancel(self):
        self.previous = None

    def suppress(self):
        self.cancel()
        self.suppressed = True

    def resume(self):
        self.cancel()
        self.suppressed = False

    def observe(self, status, items, index, mode='NORMAL', removed=False, now=None):
        now = time.monotonic() if now is None else now
        if self.suppressed:
            if status.get('state') == 'STOPPED':
                self.suppressed = False
            return None
        playlist = tuple(items)
        if playlist != self.playlist:
            self.playlist = playlist
            self.visited.clear()
        if (not status.get('online') or removed or index < 0 or index >= len(items)
                or status.get('source') != items[index]):
            self.cancel()
            return None
        key = (playlist, index, status.get('uri'))
        state = status.get('state')
        if state == 'PLAYING':
            self.visited.add(index)
            self.previous = dict(key=key, position=status.get('position', 0),
                                 duration=status.get('duration', 0), at=now,
                                 tolerance=status.get('end_tolerance', 1))
            return None
        if state == 'TRANSITIONING':
            # Short decoder transitions at EOF must not discard the last position.
            if self.previous and (self.previous['key'] != key or now - self.previous['at'] > 5):
                self.cancel()
            return None
        if state == 'PAUSED_PLAYBACK':
            # Paused time must never count as progress towards the end.
            self.cancel()
            return None
        previous = self.previous
        self.cancel()  # A single observed ending may advance only once.
        if state != 'STOPPED' or not previous or previous['key'] != key:
            return None
        duration = previous['duration']
        elapsed = max(0, min(3, now - previous['at']))
        if (status.get('error') or duration <= 0
                or previous['position'] + elapsed < duration - previous['tolerance']):
            return None
        if mode == 'REPEAT_ONE':
            return index
        if mode == 'RANDOM':
            remaining = [i for i in range(len(items)) if i not in self.visited]
            return random.choice(remaining) if remaining else None
        if index + 1 < len(items):
            return index + 1
        if mode == 'REPEAT_ALL':
            return 0
        return None
