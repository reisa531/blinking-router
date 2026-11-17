{ pkgs }:
let python = pkgs.python3.withPackages (p: [ p.cryptography ]);
in pkgs.stdenvNoCC.mkDerivation {
  pname = "blinking-panel";
  version = "0.1.0";
  src = builtins.path { path = ../.; name = "blinking-panel-source"; filter = path: type: !(builtins.elem (baseNameOf path) [ ".state" "result" "__pycache__" ".git" ]); };
  nativeBuildInputs = [ pkgs.makeWrapper ];
  dontBuild = true;
  installPhase = ''
    mkdir -p $out/lib/blinking-panel $out/bin
    cp -r blinking examples nix tests flake.nix README.md $out/lib/blinking-panel/
    cp flake.lock $out/lib/blinking-panel/
    makeWrapper ${python}/bin/python $out/bin/blinking-panel \
      --set PYTHONPATH $out/lib/blinking-panel --add-flags '-m blinking.server'
    makeWrapper ${python}/bin/python $out/bin/blinking-keepalive \
      --set PYTHONPATH $out/lib/blinking-panel --add-flags '-m blinking.keepalive'
  '';
}
