#!/bin/bash
# Typechecks ios/LearnLens without Xcode: compiles it for macOS against stub modules whose
# signatures are copied from the MWDAT 1.0.1 .swiftinterface files. Not a substitute for
# xcodebuild: the two UIKit files and the #if os(iOS) branches are not covered.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
MODS="$(mktemp -d)"
FLAGS="-target arm64-apple-macos14.0 -parse-as-library -I $MODS"
for m in MWDATCore MWDATDisplay MWDATCamera MWDATMockDevice MWDATSpeech; do
  swiftc -emit-module -module-name $m -swift-version 6 $FLAGS -emit-module-path "$MODS/$m.swiftmodule" "$HERE/stubs/$m.swift"
done
FILES=$(find "$HERE/../LearnLens" -name '*.swift' ! -name MockDisplayPreview.swift ! -name PhotoEncoding.swift ! -name PhoneCameraPicker.swift)
for v in 5 6; do
  swiftc -typecheck -swift-version $v $FLAGS $FILES "$HERE/stubs/MacShims.swift"
  echo "swift-version $v: ok"
done
