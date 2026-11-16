# The dashboard as a static site: what `pnpm run build` makes of frontend/,
# ready for a web server or GitHub Pages.
{
  lib,
  stdenvNoCC,
  nodejs,
  pnpm_11,
  fetchPnpmDeps,
  pnpmConfigHook,
  src,
  pnpmSrc,
  version,
  # Where the site is served from, such as "/qibocamp/". Unset, the paths are
  # relative, as a plain `pnpm run build` makes them, good under any prefix.
  basePath ? null,
}:
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "qibocamp-dashboard";
  inherit version src;

  pnpmRoot = "frontend";
  pnpmDeps = fetchPnpmDeps {
    inherit (finalAttrs) pname version;
    src = pnpmSrc;
    pnpm = pnpm_11;
    fetcherVersion = 5;
    hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  };

  nativeBuildInputs = [
    nodejs
    pnpm_11
    pnpmConfigHook
  ];

  env = lib.optionalAttrs (basePath != null) { BASE_PATH = basePath; };

  buildPhase = ''
    runHook preBuild
    pnpm --dir frontend run build
    runHook postBuild
  '';

  # The dashboard's own tests, as deploy.yml runs them before publishing.
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    pnpm --dir frontend run test
    runHook postCheck
  '';

  # As deploy.yml publishes it: no Jekyll, and every route served by the app.
  installPhase = ''
    runHook preInstall
    cp -r frontend/dist "$out"
    touch "$out/.nojekyll"
    cp "$out/index.html" "$out/404.html"
    runHook postInstall
  '';

  meta = {
    description = "The Qibocamp dashboard, as a static site";
    homepage = "https://qibo.science/qibocamp";
    license = lib.licenses.asl20;
    platforms = [
      "x86_64-linux"
      "aarch64-darwin"
    ];
  };
})
