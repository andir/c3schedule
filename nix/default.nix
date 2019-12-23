let
  sources = import ./sources.nix;
in import sources.nixpkgs {
  overlays = [
    (import (sources.poetry2nix + "/overlay.nix"))
    (pkgs: _: { defaultPoetryOverrides = pkgs.callPackage (import (sources.poetry2nix + "/overrides.nix")) { }; })
  ];
}
