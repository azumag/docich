# JMA earthquake XML fixtures

These unmodified XML samples are copied from the Japan Meteorological Agency sample archive published on 2026-09-17:

- Technical material and sample-list page: https://xml.kishou.go.jp/tec_material.html
- Archive: https://xml.kishou.go.jp/jmaxml_20260917_Samples.zip

| Local fixture | Archive member | Coverage | SHA-256 |
| --- | --- | --- | --- |
| `earthquake_training_empty_serial.xml` | `32-35_01_02_240613_VXSE52.xml` | JMA training message; empty Serial | `cb39f5ca648a2152a3af505db7b2fe7a83d4be7bccaf926bb340dc36376f5835` |
| `earthquake_delivery_test.xml` | `54_01_01_100514_VXSE42.xml` | JMA test status | `81155fd2bfe48fc4e384dd7f27c9f5400d79790056250ffc6a4640bf589b548d` |
| `eew_forecast_serial_32.xml` | `36_01_32_240613_VXSE44.xml` | EEW forecast, event and Serial 32 | `9c6a707facd8a1c6ab6268a87d6ebfd8b746e317920d1b2ea0204defaf5cfd9e` |
| `eew_forecast_cancel_serial_32.xml` | `36_01_33_240613_VXSE44.xml` | Cancellation for the same event and Serial 32 | `88892d8c8204db8a5ca54d4e8ec0dddf98acf4cbe731dd6a8d03910347622820` |
| `eew_warning_cancel.xml` | `37_01_03_240613_VXSE43.xml` | EEW warning cancellation | `b075dab492ed0369c8dca5203bb9b273a71dd8cdeed4be37a84de00e557fcafc` |
| `eew_prediction_cancel.xml` | `77_01_33_240613_VXSE45.xml` | EEW prediction cancellation | `9435c51f12b016c07bd0482033f3cdbc7fdf11250d8789f1db7423758a11d62b` |
| `observed_intensity_bulletin.xml` | `32-35_01_01_100806_VXSE51.xml` | Observed prefecture/area intensity, 5+ and 6- | `d88e7f2dea48d1833a0101a2e1fa44f1b25ecf09c1a278083edf6dd05d48e3cf` |
| `observed_intensity_leading_zero.xml` | `32-35_08_01_100915_VXSE51.xml` | Prefecture code 03 stays a string | `0cbe5cea84d074e4750d2db5f09d4c1521fc5f6e9f2b2a7df56e9d9d6862683f` |
| `observed_earthquake_intensity.xml` | `32-35_04_04_240613_VXSE53.xml` | Pref/Area maxima alongside separate City/station data | `f8e3e104a25b875a1e80fd0fb7f661c1d6c4ba0268055e13080aca7809df6d9e` |
| `observed_intensity_cancel.xml` | `32-35_10_01_220510_VXSE51.xml` | Observation cancellation with no Intensity and empty Serial | `230378e899a397bd0dcae616a8e36ca6780ce9831bb49a8b2c268d5e125374df` |

These are fixed historical examples for offline parsing. They are not current alerts and must not be treated as live events.

Negative tests transform copies in memory to exercise missing fields, unknown values,
training/test status, conflicting namespaces, and ambiguous duplicates. These variants
are not presented as additional official samples; the files above remain unchanged.
