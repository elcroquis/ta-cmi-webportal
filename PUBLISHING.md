# Publishing this repository in HACS

1. Create your **own public GitHub repository**, e.g. `ta-cmi-webportal`.
2. Replace `YOUR_GITHUB_USERNAME` in
   `custom_components/ta_cmi_webportal/manifest.json` with **your actual
   GitHub account**. Also set `codeowners` to `["@your-github-name"]`.
3. Publish this repository with the `custom_components/ta_cmi_webportal/`
   layout preserved. Never commit live credentials, cookies or `.storage`.
4. Run HACS and Home Assistant hassfest CI checks. Fix all issues before
   announcing a stable release. Verify with at least one **fresh HA instance**.
5. Add a brand icon using Home Assistant's brand contribution process (this
   is required for a default HACS inclusion; observe brand/trademark rules).
6. Create a release matching the manifest version, e.g. `0.4.0b1`, after
   testing. Use prereleases while compatibility is experimental.
7. Users can first add the GitHub URL in **HACS → Custom repositories**.
8. For inclusion in the default HACS catalog, follow HACS's current
   `hacs/default` contribution procedure after passing the requirements.

We have **not** created a GitHub repository, uploaded any code or submitted
this integration to HACS on your behalf.