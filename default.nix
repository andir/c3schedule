{ pkgs ? import ./nix }:
let
  overrides = pkgs.defaultPoetryOverrides // {
    praw = self: super: drv: drv.overrideAttrs (old: {
      doCheck = false;
      buildInputs = (old.buildInputs or []) ++ [ self.pytestrunner ];
    });
  };
  env = pkgs.poetry2nix.mkPoetryEnv {
    poetrylock = ./poetry.lock;
    python = pkgs.python3;
    inherit overrides;
  };
in {
  bin = pkgs.writeScriptBin "c3schedule" ''
    #! ${pkgs.stdenv.shell}
    exec "${env}/bin/sopel" "$@"
  '';

  sopelModule = ./modules/c3schedule_irc;
}
