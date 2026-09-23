// =====================================================================
// Credits -- transcribed from the dome show's Engine/docs/CREDITS.html
// (links verified there 2026-09-15). The bundle README says: reuse its text
// rather than rewriting it. Roles are condensed to one line each for a phone;
// names, links and DOIs are exactly the dome's.
// =====================================================================

export interface CreditLink { text: string; href: string }
export interface CreditItem { name: string; role: string; links: CreditLink[] }
export interface CreditGroup { title: string; items: CreditItem[] }

export const CREDITS: CreditGroup[] = [
  { title: "Solar wind driving the live feed", items: [
    { name: "NOAA SWPC real-time solar wind", role: "Speed, density and magnetic field at L1. Sets the size and shape of every surface.",
      links: [{ text: "spaceweather.gov", href: "https://www.spaceweather.gov/products/solar-wind" }] },
    { name: "SOLAR-1 spacecraft (NOAA)", role: "The spacecraft currently supplying the live feed. The readout names whichever source is active.",
      links: [{ text: "nesdis.noaa.gov", href: "https://www.nesdis.noaa.gov/our-satellites/future-programs/swfo/space-weather-observations-l1-advance-readiness-solar-1" }] },
    { name: "Planetary K-index (Kp)", role: "Global disturbance level. Sets the aurora oval's size and the T89 activity level.",
      links: [{ text: "spaceweather.gov", href: "https://www.spaceweather.gov/products/planetary-k-index" }] },
    { name: "SWPC Geospace predicted Dst", role: "Ring-current strength. A model forecast, not a measurement, and labelled that way.",
      links: [{ text: "Tóth et al. 2005", href: "https://doi.org/10.1029/2005JA011126" },
        { text: "spaceweather.gov", href: "https://www.spaceweather.gov/products/geospace-geomagnetic-activity-plot" }] },
  ] },
  { title: "Aurora", items: [
    { name: "OVATION Prime auroral model", role: "The live aurora oval: NOAA's 30-minute forecast of where aurora is likely.",
      links: [{ text: "Newell, Sotirelis & Wing 2009", href: "https://doi.org/10.1029/2009JA014326" },
        { text: "aurora forecast", href: "https://www.spaceweather.gov/products/aurora-30-minute-forecast" }] },
    { name: "Parametric Kp oval (built for this show)", role: "The oval in the replays, where no OVATION grid exists for the date. Driven by the same Kp series, and labelled as a model.",
      links: [] },
  ] },
  { title: "Magnetic field and boundary models", items: [
    { name: "Tsyganenko T89", role: "External field for the 48-hour look-back and the live shapes. Fast, Kp-driven.",
      links: [{ text: "Tsyganenko 1989", href: "https://doi.org/10.1016/0032-0633(89)90066-4" }] },
    { name: "Tsyganenko & Sitnov TS05", role: "Every stored storm. The great storms fall outside T96's fitted range.",
      links: [{ text: "Tsyganenko & Sitnov 2005", href: "https://doi.org/10.1029/2004JA010798" },
        { text: "CCMC model page", href: "https://ccmc.gsfc.nasa.gov/models/Tsyganenko%20Magnetic%20Field~TS05/" }] },
    { name: "Tsyganenko T96", role: "The quiet-to-moderate storm model, checked against for range.",
      links: [{ text: "Tsyganenko 1995", href: "https://doi.org/10.1029/94JA03193" },
        { text: "Tsyganenko & Stern 1996", href: "https://doi.org/10.1029/96JA02735" }] },
    { name: "IGRF-14 internal field", role: "Earth's own field close in, where the planet's interior dominates.",
      links: [{ text: "NCEI / IAGA", href: "https://www.ncei.noaa.gov/products/international-geomagnetic-reference-field" },
        { text: "Beggan et al. 2026", href: "https://doi.org/10.1186/s40623-025-02360-0" }] },
    { name: "Lin magnetopause", role: "The magnetopause's 3-D shape: the cusp indentations, the dawn-dusk and north-south asymmetry, the lean with the dipole tilt.",
      links: [{ text: "Lin et al. 2010", href: "https://doi.org/10.1029/2009JA014235" }] },
    { name: "Shue magnetopause", role: "The nose distance outside Lin's fitted range (Bz −15 to +5 nT, 1 to 16 nPa). Every great storm's peak sits on Shue, so the shell is a hybrid.",
      links: [{ text: "Shue et al. 1998", href: "https://doi.org/10.1029/98JA01103" },
        { text: "Shue et al. 1997", href: "https://doi.org/10.1029/97JA00196" }] },
    { name: "Bow shock", role: "Slavin & Holzer give the shape; Farris & Russell set how far it stands ahead of the magnetopause.",
      links: [{ text: "Slavin & Holzer 1981", href: "https://doi.org/10.1029/JA086iA13p11401" },
        { text: "Farris & Russell 1994", href: "https://doi.org/10.1029/94JA01020" }] },
    { name: "geopack (Sheng Tian)", role: "The Python Tsyganenko models, IGRF and coordinate transforms every traced line passes through.",
      links: [{ text: "github.com/tsssss/geopack", href: "https://github.com/tsssss/geopack" },
        { text: "Zenodo release", href: "https://doi.org/10.5281/zenodo.15110787" }] },
  ] },
  { title: "The plasma populations", items: [
    { name: "Plasmasphere", role: "Size and shape are published: the plasmapause sits at L = 5.6 − 0.46 × the previous day's peak Kp. It does not draw the duskside plume.",
      links: [{ text: "Carpenter & Anderson 1992", href: "https://doi.org/10.1029/91JA01548" },
        { text: "O'Brien & Moldwin 2003", href: "https://doi.org/10.1029/2002GL016007" }] },
    { name: "Ring current", role: "Its energy follows from the measured Dst / SYM-H; where that energy sits is climatology, drawn, and labelled so.",
      links: [{ text: "Dessler & Parker 1959", href: "https://doi.org/10.1029/JZ064i012p02239" },
        { text: "Sckopke 1966", href: "https://doi.org/10.1029/JZ071i013p03125" }] },
    { name: "Solar-wind shock jump", role: "Parcels slow by the Rankine-Hugoniot factor as they cross the bow shock, and bunch up in the sheath.",
      links: [{ text: "Farris & Russell 1994", href: "https://doi.org/10.1029/94JA01020" }] },
    { name: "Interplanetary field lines", role: "Carried through the wind's own flow; they turn blue where Kan & Lee's merging rate says they have connected to Earth's field.",
      links: [{ text: "Kan & Lee 1979", href: "https://doi.org/10.1029/GL006i007p00577" }] },
  ] },
  { title: "Historical data for the replays", items: [
    { name: "OMNI 1-minute (NASA/GSFC)", role: "Drives all five stored storms, already time-shifted to the bow shock nose.",
      links: [{ text: "OMNI docs", href: "https://omniweb.gsfc.nasa.gov/html/HROdocum.html" },
        { text: "King & Papitashvili 2005", href: "https://doi.org/10.1029/2004JA010649" }] },
    { name: "CDAWeb and the HAPI standard", role: "How the storm archives are fetched.",
      links: [{ text: "cdaweb.gsfc.nasa.gov", href: "https://cdaweb.gsfc.nasa.gov/" }, { text: "hapi-server.org", href: "https://hapi-server.org/" },
        { text: "Weigel et al. 2021", href: "https://doi.org/10.1029/2021JA029534" }] },
    { name: "CCMC ISWA", role: "The 48-hour look-back. OMNI lags by weeks, so recent days come from here.",
      links: [{ text: "iswa.ccmc.gsfc.nasa.gov", href: "https://iswa.ccmc.gsfc.nasa.gov/app/" }] },
    { name: "ACE spacecraft · MAG and SWEPAM", role: "The field and plasma measurements behind the look-back.",
      links: [{ text: "science.nasa.gov", href: "https://science.nasa.gov/mission/ace/" },
        { text: "Smith et al. 1998", href: "https://doi.org/10.1023/A:1005092216668" },
        { text: "McComas et al. 1998", href: "https://doi.org/10.1023/A:1005040232597" }] },
    { name: "Kyoto WDC Dst and SYM-H", role: "Ring-current strength for the replays.",
      links: [{ text: "Dst index", href: "https://wdc.kugi.kyoto-u.ac.jp/dstdir/" },
        { text: "ASY/SYM", href: "https://wdc.kugi.kyoto-u.ac.jp/aeasy/index.html" }] },
    { name: "GFZ Kp index", role: "The Kp series for the look-back and the replays.",
      links: [{ text: "kp.gfz.de", href: "https://kp.gfz.de/en/" }, { text: "Matzka et al. 2021", href: "https://doi.org/10.1029/2020SW002641" }] },
  ] },
];

export const STORM_REFERENCES: Record<string, { why: string; links: CreditLink[] }> = {
  bastille: { why: "Two magnetic clouds and their shocks; the magnetopause is driven far inside its usual stand.",
    links: [{ text: "Lepping et al. 2001", href: "https://doi.org/10.1023/A:1014264327855" }] },
  nov2003: { why: "The largest geomagnetic storm of solar cycle 23.",
    links: [{ text: "Gopalswamy et al. 2005", href: "https://doi.org/10.1029/2004GL021639" }] },
  gannon: { why: "The first severe (G5) storm in two decades; named for space weather physicist Jennifer Gannon.",
    links: [{ text: "Hayakawa et al. 2025", href: "https://doi.org/10.3847/1538-4357/ad9335" },
      { text: "NASA Science", href: "https://science.nasa.gov/science-research/heliophysics/what-nasa-is-learning-from-the-biggest-geomagnetic-storm-in-20-years/" }] },
  octg4: { why: "Sympathetic eruptions; the second strongest storm of cycle 25 so far.",
    links: [{ text: "Wang et al. 2026", href: "https://doi.org/10.3847/2041-8213/ae5801" }] },
  jan2026: { why: "Too recent for a peer-reviewed overview. Replayed straight from the OMNI record, with no interpretive claims attached.",
    links: [] },
};

/** The dome panel's one-paragraph credits footer, verbatim. */
export const CREDITS_SUMMARY =
  "Live wind, Kp and forecast Dst: NOAA SWPC (active L1 spacecraft). Live aurora: OVATION Prime. " +
  "Last 48 h: CCMC ISWA (ACE, GFZ, Kyoto). Storms: NASA/GSFC CDAWeb OMNI. Field: Tsyganenko T89 / T96 / TS05 " +
  "with IGRF-14 through geopack. Magnetopause: Lin et al. 2010 shape on a subsolar distance blended to Shue et al. " +
  "1998 outside Lin's range. Bow shock: Slavin and Holzer with the Farris and Russell standoff. Ring current: " +
  "Dessler-Parker-Sckopke (energy measured, radial shape drawn). Plasmapause: Carpenter and Anderson 1992. " +
  "IMF merging: Kan and Lee 1979.";
