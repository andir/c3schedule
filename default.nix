{ pkgs ? import ./nix }:
let
  overrides = pkgs.poetry2nix.overrides.withDefaults (self: super: {
    praw = super.praw.overrideAttrs (old: {
      doCheck = false;
      buildInputs = (old.buildInputs or [ ]) ++ [ self.pytestrunner ];
    });

    sopel = super.sopel.overrideAttrs (old: {
      patches = [ ./sopel-flood.patch ];
    });
  });

  env = pkgs.poetry2nix.mkPoetryEnv {
    poetrylock = ./poetry.lock;
    pyproject = ./pyproject.toml;
    python = pkgs.python3;
    inherit overrides;
  };
in
{
  inherit env;
  bin = pkgs.writeScriptBin "c3schedule" ''
    #! ${pkgs.stdenv.shell}
    exec "${env}/bin/sopel" "$@"
  '';

  sopelModule = ./modules/c3schedule_irc;
}
