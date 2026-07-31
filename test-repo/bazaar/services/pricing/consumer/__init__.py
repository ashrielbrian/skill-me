"""Consumer pipeline for the pricing stream.

Stages: decode -> dedupe -> match -> apply -> stats -> ack. The loop in
loop.py drives one batch at a time through these stages.
"""
