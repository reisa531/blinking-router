{ pkgs, src }:
pkgs.rustPlatform.buildRustPackage {
  pname = "net-loginer";
  version = "0.5.0-blinking";
  inherit src;
  cargoLock.lockFile = src + "/Cargo.lock";
  nativeBuildInputs = [ pkgs.pkg-config pkgs.python3 ];
  buildInputs = [ pkgs.openssl pkgs.onnxruntime ];
  ORT_LIB_LOCATION = "${pkgs.onnxruntime}/lib";
  ORT_PREFER_DYNAMIC_LINK = "1";
  postPatch = ''python ${./patch-loginer.py}'';
  doCheck = false; # Integration exercises the patched CLI without campus credentials.
  meta.license = pkgs.lib.licenses.mit;
}
