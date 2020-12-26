let
  pkgs = import ./nix;
  c3s = import ./default.nix { inherit pkgs; };
in
pkgs.mkShell {
  nativeBuildInputs = with pkgs; [ niv c3s.env ];
}
