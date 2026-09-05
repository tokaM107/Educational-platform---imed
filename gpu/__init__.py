"""The GPU-side transcription service. Runs on the RunPod pod, not the server.

Kept out of the deployed API image on purpose — see the Dockerfile, which
copies `app` and a named subset of `rag` and nothing from here.
"""
