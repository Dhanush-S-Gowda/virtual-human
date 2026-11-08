# Browser avatar engine

This folder contains the TalkingHead runtime used by the app: avatar rendering,
lip-sync language modules, dynamic bones, retargeting, and the playback worklet.
Application-specific scheduling lives in [avatar](../avatar/README.md).

The shared [TalkingHead directory](../../../TalkingHead/README.md) contains
upstream documentation and assets. Preserve its license and review local
integration changes before replacing engine files with an upstream version.

Run [frontend tests](../../tests/README.md) after changes to playback or lip sync.
