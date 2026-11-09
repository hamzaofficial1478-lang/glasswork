/* currency.js — Botix prices in the visitor's own money.

   The prices are set in US dollars (Starter, Growth, Pro; with your own AI key
   or with Botix AI). This shows them in the visitor's currency at today's rate:
   Rs in Pakistan, riyals in Saudi Arabia, dirhams in the UAE, and so on.

   How the currency is chosen, on the visitor's own device and without asking
   anyone: the device's time zone (Asia/Karachi -> Pakistan -> PKR), or failing
   that the region in its language setting (en-PK). Anyone can change it in the
   list. Nothing about the visitor is sent anywhere for this.

   Today's rates come from a public file of exchange rates, updated daily
   (jsDelivr, with a second address if the first is down). If neither answers,
   the rates saved below are used and the note says from which day. People are
   billed in US dollars, and the page says so. */
(function () {
  'use strict';
  var RATES_AT = ['https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json',
                  'https://latest.currency-api.pages.dev/v1/currencies/usd.min.json'];
  var SAVED = { date: '2026-10-12', usd: {"pkr":276.751363,"sar":3.75,"aed":3.6725,"qar":3.64,"kwd":0.311049,"omr":0.384961,"bhd":0.376,"inr":95.740531,"bdt":123.254203,"egp":52.582238,"gbp":0.755529,"eur":0.892822,"cad":1.426126,"aud":1.433893,"try":49.335196,"myr":4.084053,"sgd":1.280237,"idr":17912.816691,"zar":16.507289,"ngn":1331.585733,"kes":129.74429,"jod":0.709,"mad":9.777485,"lkr":330.682632,"npr":153.256655} };
  var LIST = ["USD", "PKR", "SAR", "AED", "QAR", "KWD", "OMR", "BHD", "INR", "BDT", "EGP", "GBP", "EUR", "CAD", "AUD", "TRY", "MYR", "SGD", "IDR", "ZAR", "NGN", "KES", "JOD", "MAD", "LKR", "NPR"];
  var ZONES = {"AD":"Europe/Andorra","AE":"Asia/Dubai","AF":"Asia/Kabul","AG":"America/Antigua","AI":"America/Anguilla","AL":"Europe/Tirane","AM":"Asia/Yerevan","AO":"Africa/Luanda","AQ":"Antarctica/Casey Antarctica/Davis Antarctica/DumontDUrville Antarctica/Mawson Antarctica/McMurdo Antarctica/Palmer Antarctica/Rothera Antarctica/Syowa Antarctica/Troll Antarctica/Vostok","AR":"America/Argentina/Buenos_Aires America/Argentina/Catamarca America/Argentina/Cordoba America/Argentina/Jujuy America/Argentina/La_Rioja America/Argentina/Mendoza America/Argentina/Rio_Gallegos America/Argentina/Salta America/Argentina/San_Juan America/Argentina/San_Luis America/Argentina/Tucuman America/Argentina/Ushuaia America/Buenos_Aires","AS":"Pacific/Pago_Pago Pacific/Samoa","AT":"Europe/Vienna","AU":"Antarctica/Macquarie Australia/ACT Australia/Adelaide Australia/Brisbane Australia/Broken_Hill Australia/Darwin Australia/Eucla Australia/Hobart Australia/Lindeman Australia/Lord_Howe Australia/Melbourne Australia/NSW Australia/Perth Australia/Sydney","AW":"America/Aruba","AX":"Europe/Mariehamn","AZ":"Asia/Baku","BA":"Europe/Sarajevo","BB":"America/Barbados","BD":"Asia/Dacca Asia/Dhaka","BE":"Europe/Brussels","BF":"Africa/Ouagadougou","BG":"Europe/Sofia","BH":"Asia/Bahrain","BI":"Africa/Bujumbura","BJ":"Africa/Porto-Novo","BL":"America/St_Barthelemy","BM":"Atlantic/Bermuda","BN":"Asia/Brunei","BO":"America/La_Paz","BQ":"America/Kralendijk","BR":"America/Araguaina America/Bahia America/Belem America/Boa_Vista America/Campo_Grande America/Cuiaba America/Eirunepe America/Fortaleza America/Maceio America/Manaus America/Noronha America/Porto_Velho America/Recife America/Rio_Branco America/Santarem America/Sao_Paulo","BS":"America/Nassau","BT":"Asia/Thimbu Asia/Thimphu","BW":"Africa/Gaborone","BY":"Europe/Minsk","BZ":"America/Belize","CA":"America/Atikokan America/Blanc-Sablon America/Cambridge_Bay America/Creston America/Dawson America/Dawson_Creek America/Edmonton America/Fort_Nelson America/Glace_Bay America/Goose_Bay America/Halifax America/Inuvik America/Iqaluit America/Moncton America/Rankin_Inlet America/Regina America/Resolute America/St_Johns America/Swift_Current America/Toronto America/Vancouver America/Whitehorse America/Winnipeg Canada/Central Canada/Eastern Canada/Pacific","CC":"Indian/Cocos","CD":"Africa/Kinshasa Africa/Lubumbashi","CF":"Africa/Bangui","CG":"Africa/Brazzaville","CH":"Europe/Zurich","CI":"Africa/Abidjan","CK":"Pacific/Rarotonga","CL":"America/Coyhaique America/Punta_Arenas America/Santiago Pacific/Easter","CM":"Africa/Douala","CN":"Asia/Chongqing Asia/Harbin Asia/Kashgar Asia/Shanghai Asia/Urumqi","CO":"America/Bogota","CR":"America/Costa_Rica","CU":"America/Havana","CV":"Atlantic/Cape_Verde","CW":"America/Curacao","CX":"Indian/Christmas","CY":"Asia/Famagusta Asia/Nicosia Europe/Nicosia","CZ":"Europe/Prague","DE":"Europe/Berlin Europe/Busingen","DJ":"Africa/Djibouti","DK":"Europe/Copenhagen","DM":"America/Dominica","DO":"America/Santo_Domingo","DZ":"Africa/Algiers","EC":"America/Guayaquil Pacific/Galapagos","EE":"Europe/Tallinn","EG":"Africa/Cairo","EH":"Africa/El_Aaiun","ER":"Africa/Asmara","ES":"Africa/Ceuta Atlantic/Canary Europe/Madrid","ET":"Africa/Addis_Ababa","FI":"Europe/Helsinki","FJ":"Pacific/Fiji","FK":"Atlantic/Stanley","FM":"Pacific/Chuuk Pacific/Kosrae Pacific/Pohnpei","FO":"Atlantic/Faeroe Atlantic/Faroe","FR":"Europe/Paris","GA":"Africa/Libreville","GB":"Europe/London","GD":"America/Grenada","GE":"Asia/Tbilisi","GF":"America/Cayenne","GG":"Europe/Guernsey","GH":"Africa/Accra","GI":"Europe/Gibraltar","GL":"America/Danmarkshavn America/Nuuk America/Scoresbysund America/Thule","GM":"Africa/Banjul","GN":"Africa/Conakry","GP":"America/Guadeloupe","GQ":"Africa/Malabo","GR":"Europe/Athens","GS":"Atlantic/South_Georgia","GT":"America/Guatemala","GU":"Pacific/Guam","GW":"Africa/Bissau","GY":"America/Guyana","HK":"Asia/Hong_Kong","HN":"America/Tegucigalpa","HR":"Europe/Zagreb","HT":"America/Port-au-Prince","HU":"Europe/Budapest","ID":"Asia/Jakarta Asia/Jayapura Asia/Makassar Asia/Pontianak Asia/Ujung_Pandang","IE":"Europe/Dublin","IL":"Asia/Jerusalem Asia/Tel_Aviv","IM":"Europe/Isle_of_Man","IN":"Asia/Calcutta Asia/Kolkata","IO":"Indian/Chagos","IQ":"Asia/Baghdad","IR":"Asia/Tehran","IS":"Atlantic/Reykjavik","IT":"Europe/Rome","JE":"Europe/Jersey","JM":"America/Jamaica","JO":"Asia/Amman","JP":"Asia/Tokyo","KE":"Africa/Nairobi","KG":"Asia/Bishkek","KH":"Asia/Phnom_Penh","KI":"Pacific/Kanton Pacific/Kiritimati Pacific/Tarawa","KM":"Indian/Comoro","KN":"America/St_Kitts","KP":"Asia/Pyongyang","KR":"Asia/Seoul","KW":"Asia/Kuwait","KY":"America/Cayman","KZ":"Asia/Almaty Asia/Aqtau Asia/Aqtobe Asia/Atyrau Asia/Oral Asia/Qostanay Asia/Qyzylorda","LA":"Asia/Vientiane","LB":"Asia/Beirut","LC":"America/St_Lucia","LI":"Europe/Vaduz","LK":"Asia/Colombo","LR":"Africa/Monrovia","LS":"Africa/Maseru","LT":"Europe/Vilnius","LU":"Europe/Luxembourg","LV":"Europe/Riga","LY":"Africa/Tripoli","MA":"Africa/Casablanca","MC":"Europe/Monaco","MD":"Europe/Chisinau","ME":"Europe/Podgorica","MF":"America/Marigot","MG":"Indian/Antananarivo","MH":"Pacific/Kwajalein Pacific/Majuro","MK":"Europe/Skopje","ML":"Africa/Bamako","MM":"Asia/Rangoon Asia/Yangon","MN":"Asia/Hovd Asia/Ulaanbaatar Asia/Ulan_Bator","MO":"Asia/Macao Asia/Macau","MP":"Pacific/Saipan","MQ":"America/Martinique","MR":"Africa/Nouakchott","MS":"America/Montserrat","MT":"Europe/Malta","MU":"Indian/Mauritius","MV":"Indian/Maldives","MW":"Africa/Blantyre","MX":"America/Bahia_Banderas America/Cancun America/Chihuahua America/Ciudad_Juarez America/Hermosillo America/Matamoros America/Mazatlan America/Merida America/Mexico_City America/Monterrey America/Ojinaga America/Tijuana","MY":"Asia/Kuala_Lumpur Asia/Kuching","MZ":"Africa/Maputo","NA":"Africa/Windhoek","NC":"Pacific/Noumea","NE":"Africa/Niamey","NF":"Pacific/Norfolk","NG":"Africa/Lagos","NI":"America/Managua","NL":"Europe/Amsterdam","NO":"Europe/Oslo","NP":"Asia/Kathmandu Asia/Katmandu","NR":"Pacific/Nauru","NU":"Pacific/Niue","NZ":"Pacific/Auckland Pacific/Chatham","OM":"Asia/Muscat","PA":"America/Panama","PE":"America/Lima","PF":"Pacific/Gambier Pacific/Marquesas Pacific/Tahiti","PG":"Pacific/Bougainville Pacific/Port_Moresby","PH":"Asia/Manila","PK":"Asia/Karachi","PL":"Europe/Warsaw","PM":"America/Miquelon","PN":"Pacific/Pitcairn","PR":"America/Puerto_Rico","PS":"Asia/Gaza Asia/Hebron","PT":"Atlantic/Azores Atlantic/Madeira Europe/Lisbon","PW":"Pacific/Palau","PY":"America/Asuncion","QA":"Asia/Qatar","RE":"Indian/Reunion","RO":"Europe/Bucharest","RS":"Europe/Belgrade","RU":"Asia/Anadyr Asia/Barnaul Asia/Chita Asia/Irkutsk Asia/Kamchatka Asia/Khandyga Asia/Krasnoyarsk Asia/Magadan Asia/Novokuznetsk Asia/Novosibirsk Asia/Omsk Asia/Sakhalin Asia/Srednekolymsk Asia/Tomsk Asia/Ust-Nera Asia/Vladivostok Asia/Yakutsk Asia/Yekaterinburg Europe/Astrakhan Europe/Kaliningrad Europe/Kirov Europe/Moscow Europe/Samara Europe/Saratov Europe/Ulyanovsk Europe/Volgograd","RW":"Africa/Kigali","SA":"Asia/Riyadh","SB":"Pacific/Guadalcanal","SC":"Indian/Mahe","SD":"Africa/Khartoum","SE":"Europe/Stockholm","SG":"Asia/Singapore","SH":"Atlantic/St_Helena","SI":"Europe/Ljubljana","SJ":"Arctic/Longyearbyen","SK":"Europe/Bratislava","SL":"Africa/Freetown","SM":"Europe/San_Marino","SN":"Africa/Dakar","SO":"Africa/Mogadishu","SR":"America/Paramaribo","SS":"Africa/Juba","ST":"Africa/Sao_Tome","SV":"America/El_Salvador","SX":"America/Lower_Princes","SY":"Asia/Damascus","SZ":"Africa/Mbabane","TC":"America/Grand_Turk","TD":"Africa/Ndjamena","TF":"Indian/Kerguelen","TG":"Africa/Lome","TH":"Asia/Bangkok","TJ":"Asia/Dushanbe","TK":"Pacific/Fakaofo","TL":"Asia/Dili","TM":"Asia/Ashgabat","TN":"Africa/Tunis","TO":"Pacific/Tongatapu","TR":"Asia/Istanbul Europe/Istanbul","TT":"America/Port_of_Spain","TV":"Pacific/Funafuti","TW":"Asia/Taipei","TZ":"Africa/Dar_es_Salaam","UA":"Europe/Kiev Europe/Kyiv Europe/Simferopol","UG":"Africa/Kampala","UM":"Pacific/Midway Pacific/Wake","US":"America/Adak America/Anchorage America/Boise America/Chicago America/Denver America/Detroit America/Indiana/Indianapolis America/Indiana/Knox America/Indiana/Marengo America/Indiana/Petersburg America/Indiana/Tell_City America/Indiana/Vevay America/Indiana/Vincennes America/Indiana/Winamac America/Indianapolis America/Juneau America/Kentucky/Louisville America/Kentucky/Monticello America/Los_Angeles America/Menominee America/Metlakatla America/New_York America/Nome America/North_Dakota/Beulah America/North_Dakota/Center America/North_Dakota/New_Salem America/Phoenix America/Sitka America/Yakutat Pacific/Honolulu US/Alaska US/Central US/Eastern US/Hawaii US/Mountain US/Pacific","UY":"America/Montevideo","UZ":"Asia/Samarkand Asia/Tashkent","VA":"Europe/Vatican","VC":"America/St_Vincent","VE":"America/Caracas","VG":"America/Tortola","VI":"America/St_Thomas","VN":"Asia/Ho_Chi_Minh Asia/Saigon","VU":"Pacific/Efate","WF":"Pacific/Wallis","WS":"Pacific/Apia","YE":"Asia/Aden","YT":"Indian/Mayotte","ZA":"Africa/Johannesburg","ZM":"Africa/Lusaka","ZW":"Africa/Harare"};
  var CURRENCY = {"AD":"EUR","AE":"AED","AF":"AFN","AG":"XCD","AI":"XCD","AL":"ALL","AM":"AMD","AO":"AOA","AQ":"USD","AR":"ARS","AS":"USD","AT":"EUR","AU":"AUD","AW":"AWG","AX":"EUR","AZ":"AZN","BA":"BAM","BB":"BBD","BD":"BDT","BE":"EUR","BF":"XOF","BG":"EUR","BH":"BHD","BI":"BIF","BJ":"XOF","BL":"EUR","BM":"BMD","BN":"BND","BO":"BOB","BQ":"USD","BR":"BRL","BS":"BSD","BT":"BTN","BW":"BWP","BY":"BYN","BZ":"BZD","CA":"CAD","CC":"AUD","CD":"CDF","CF":"XAF","CG":"XAF","CH":"CHF","CI":"XOF","CK":"NZD","CL":"CLP","CM":"XAF","CN":"CNY","CO":"COP","CR":"CRC","CU":"CUP","CV":"CVE","CW":"ANG","CX":"AUD","CY":"EUR","CZ":"CZK","DE":"EUR","DJ":"DJF","DK":"DKK","DM":"XCD","DO":"DOP","DZ":"DZD","EC":"USD","EE":"EUR","EG":"EGP","EH":"MAD","ER":"ERN","ES":"EUR","ET":"ETB","FI":"EUR","FJ":"FJD","FK":"FKP","FM":"USD","FO":"DKK","FR":"EUR","GA":"XAF","GB":"GBP","GD":"XCD","GE":"GEL","GF":"EUR","GG":"GBP","GH":"GHS","GI":"GIP","GL":"DKK","GM":"GMD","GN":"GNF","GP":"EUR","GQ":"XAF","GR":"EUR","GS":"GBP","GT":"GTQ","GU":"USD","GW":"XOF","GY":"GYD","HK":"HKD","HN":"HNL","HR":"EUR","HT":"HTG","HU":"HUF","ID":"IDR","IE":"EUR","IL":"ILS","IM":"GBP","IN":"INR","IO":"USD","IQ":"IQD","IR":"IRR","IS":"ISK","IT":"EUR","JE":"GBP","JM":"JMD","JO":"JOD","JP":"JPY","KE":"KES","KG":"KGS","KH":"KHR","KI":"AUD","KM":"KMF","KN":"XCD","KP":"KPW","KR":"KRW","KW":"KWD","KY":"KYD","KZ":"KZT","LA":"LAK","LB":"LBP","LC":"XCD","LI":"CHF","LK":"LKR","LR":"LRD","LS":"LSL","LT":"EUR","LU":"EUR","LV":"EUR","LY":"LYD","MA":"MAD","MC":"EUR","MD":"MDL","ME":"EUR","MF":"EUR","MG":"MGA","MH":"USD","MK":"MKD","ML":"XOF","MM":"MMK","MN":"MNT","MO":"MOP","MP":"USD","MQ":"EUR","MR":"MRU","MS":"XCD","MT":"EUR","MU":"MUR","MV":"MVR","MW":"MWK","MX":"MXN","MY":"MYR","MZ":"MZN","NA":"NAD","NC":"XPF","NE":"XOF","NF":"AUD","NG":"NGN","NI":"NIO","NL":"EUR","NO":"NOK","NP":"NPR","NR":"AUD","NU":"NZD","NZ":"NZD","OM":"OMR","PA":"USD","PE":"PEN","PF":"XPF","PG":"PGK","PH":"PHP","PK":"PKR","PL":"PLN","PM":"EUR","PN":"NZD","PR":"USD","PS":"ILS","PT":"EUR","PW":"USD","PY":"PYG","QA":"QAR","RE":"EUR","RO":"RON","RS":"RSD","RU":"RUB","RW":"RWF","SA":"SAR","SB":"SBD","SC":"SCR","SD":"SDG","SE":"SEK","SG":"SGD","SH":"SHP","SI":"EUR","SJ":"NOK","SK":"EUR","SL":"SLE","SM":"EUR","SN":"XOF","SO":"SOS","SR":"SRD","SS":"SSP","ST":"STN","SV":"USD","SX":"ANG","SY":"SYP","SZ":"SZL","TC":"USD","TD":"XAF","TF":"EUR","TG":"XOF","TH":"THB","TJ":"TJS","TK":"NZD","TL":"USD","TM":"TMT","TN":"TND","TO":"TOP","TR":"TRY","TT":"TTD","TV":"AUD","TW":"TWD","TZ":"TZS","UA":"UAH","UG":"UGX","UM":"USD","US":"USD","UY":"UYU","UZ":"UZS","VA":"EUR","VC":"XCD","VE":"VES","VG":"USD","VI":"USD","VN":"VND","VU":"VUV","WF":"XPF","WS":"WST","YE":"YER","YT":"EUR","ZA":"ZAR","ZM":"ZMW","ZW":"USD"};

  var box = document.querySelector('[data-pricing]');
  if (!box) return;
  var $$ = function (q) { return Array.prototype.slice.call(box.querySelectorAll(q)); };
  var state = { mode: 'own', cur: 'USD', country: 'US', rates: SAVED.usd, date: SAVED.date, live: false };

  function countryOfZone(tz) {
    for (var cc in ZONES) if ((' ' + ZONES[cc] + ' ').indexOf(' ' + tz + ' ') >= 0) return cc;
    return '';
  }
  function guess() {
    var cc = '';
    try { cc = countryOfZone(Intl.DateTimeFormat().resolvedOptions().timeZone || ''); } catch (e) { /* old browser */ }
    if (!cc) {
      var langs = navigator.languages || [navigator.language || ''];
      for (var i = 0; i < langs.length && !cc; i++) {
        var m = /[-_]([A-Za-z]{2})$/.exec(langs[i] || '');
        if (m && CURRENCY[m[1].toUpperCase()]) cc = m[1].toUpperCase();
      }
    }
    return cc || 'US';
  }
  function name(cur) {
    try { return new Intl.DisplayNames(['en'], { type: 'currency' }).of(cur); } catch (e) { return cur; }
  }
  function money(v, cur) {
    var d = v >= 100 ? 0 : 2, loc = 'en-' + state.country;
    var o = { style: 'currency', currency: cur, minimumFractionDigits: d, maximumFractionDigits: d };
    try { return new Intl.NumberFormat(loc, Object.assign({ currencyDisplay: 'narrowSymbol' }, o)).format(v); }
    catch (e) { try { return new Intl.NumberFormat('en', o).format(v); } catch (e2) { return cur + ' ' + v.toFixed(d); } }
  }
  function rate(cur) { return cur === 'USD' ? 1 : state.rates[cur.toLowerCase()]; }

  function show() {
    var cur = rate(state.cur) ? state.cur : 'USD', r = rate(cur);
    $$('[data-usd-own]').forEach(function (el) {
      var usd = Number(el.getAttribute(state.mode === 'ai' ? 'data-usd-ai' : 'data-usd-own'));
      el.textContent = money(usd * r, cur);
      var line = el.parentNode.querySelector('[data-usd-line]');
      if (line) line.textContent = cur === 'USD' ? 'a month' : 'a month, US$' + usd;
    });
    $$('[data-ai-note]').forEach(function (el) { el.hidden = state.mode !== 'ai'; });
    $$('[data-own-note]').forEach(function (el) { el.hidden = state.mode === 'ai'; });
    var note = box.querySelector('[data-cur-note]');
    if (note) {
      var day = new Date(state.date + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
      note.textContent = cur === 'USD' ? 'Prices are in US dollars, and that is what you are billed in.'
        : 'Shown in ' + name(cur) + ' at the rate on ' + day + '. You are billed in US dollars, so your bank\'s amount may differ a little.';
    }
  }

  // the list of currencies, with the visitor's own first if it is not already in it
  var sel = box.querySelector('[data-cur-select]');
  function fill() {
    if (!sel) return;
    var list = LIST.slice();
    if (list.indexOf(state.cur) < 0 && rate(state.cur)) list.splice(1, 0, state.cur);
    sel.innerHTML = list.filter(rate).map(function (c) {
      return '<option value="' + c + '"' + (c === state.cur ? ' selected' : '') + '>' + c + ' · ' + name(c) + '</option>';
    }).join('');
  }
  if (sel) sel.addEventListener('change', function () { state.cur = sel.value; show(); });

  // own AI key, or Botix AI
  $$('[data-price-switch] button').forEach(function (b) {
    b.addEventListener('click', function () {
      state.mode = b.getAttribute('data-mode');
      $$('[data-price-switch] button').forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); });
      show();
    });
  });

  state.country = guess();
  state.cur = CURRENCY[state.country] || 'USD';
  fill(); show();

  // today's rates
  function load(i) {
    if (i >= RATES_AT.length || !window.fetch) return;
    fetch(RATES_AT[i], { credentials: 'omit', referrerPolicy: 'no-referrer' }).then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    }).then(function (j) {
      if (!j || !j.usd || !j.date) throw new Error('no rates');
      state.rates = j.usd; state.date = j.date; state.live = true;
      fill(); show();
    }).catch(function () { load(i + 1); });
  }
  load(0);
})();
