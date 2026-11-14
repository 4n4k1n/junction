# Demo assets

`desk.jpg` (optional, not in git): a photo of your UNO Q and KY-037 on the desk. In demo mode the
backend uses it when a request arrives without a photo, so the flow still starts if the glasses
camera fails. Demo mode never sends it to an AI: the analysis is a saved answer.

Save the same photo in the iPhone's photo library as well. "Pick a photo and start" then works as the
capture fallback in normal mode, where the photo really is analyzed.
