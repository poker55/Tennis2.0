# Daily Report - 01.08.2026

Today we built and tested the first compact prototype for one-camera tennis-ball bounce and in/out detection using classical OpenCV only.

The prototype includes:

- Manual singles-side court calibration
- Court-line suggestion overlay using Hough lines
- Ball detection using background subtraction and motion masks
- Optional tennis-ball HSV color filtering
- Kalman prediction
- Bounce candidate scoring
- In/out classification using the calibrated singles-side polygon
- Debug video export
- Per-frame CSV logging

Initial testing was not satisfying. The main issue is that the detector often misclassified player shadows, body motion, racket motion, or other moving regions as the tennis ball. The image contains a lot of visual noise, and the tennis ball is difficult to locate reliably using the current background-removal technique.

We tried several adjustments:

1. Motion-only detection

   Removed color matching and used only background subtraction.

   Result: static scoreboard false positives improved, but moving shadows became a major problem.

2. Color-assisted moving-object detection

   Reintroduced yellow/green tennis-ball HSV matching, but only for moving blobs.

   Result: better in theory, but still not robust enough.

3. Stricter filtering

   Added tighter area and bounding-box limits, brightness rejection, and stronger color scoring.

   Result: still not reliable enough.

4. OpenCV shadow suppression

   Enabled MOG2 `detectShadows=True` and thresholded the foreground mask to remove shadow-labeled pixels.

   Result: shadow false positives still appeared, suggesting the foreground mask needs deeper inspection.

## Conclusion

We have not yet developed a satisfying first version of the one-camera algorithm. The current test results show that background removal alone is too noisy for this video. The detector still confuses moving shadows and other player-related motion with the tennis ball.

## Recommended Next Step

Before adding more detection logic, we should generate debug outputs for the intermediate image-processing stages. Specifically, we should save sample frames showing:

- Raw frame
- Foreground mask from background subtraction
- Shadow mask / removed shadow pixels
- Motion-only mask
- Color mask
- Combined candidate mask
- Detected contours before filtering
- Accepted and rejected contour boxes with rejection reasons

This will let us see exactly what OpenCV detects as a moving object and why the shadow is often mistaken for the ball. The next goal should be to diagnose the mask quality first, then tune or redesign the ball detector based on real visual evidence instead of guessing thresholds.
