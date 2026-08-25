<?php
// ============================================================================
//  PASTE THIS WHOLE FILE'S CONTENTS INTO YOUR HEADER INCLUDE,
//  JUST ABOVE THE LINE THAT SAYS  </head>
//
//  You said you have one header include shared by all your pages. Good —
//  that means ONE paste, in ONE file, and you are finished. You do not have
//  to touch index.php, service.php, about.php, gallery.php, blog.php,
//  faq.php or contact.php individually.
//
//  Nothing below changes how any page LOOKS. Not one pixel. It is the
//  Chamber-of-Commerce form for Google, filled in.
//
//  BEFORE YOU SAVE, CHANGE THE TWO LINES MARKED  <<< CHANGE THIS
// ============================================================================
?>

<?php // ---- PART 1: your business card. Appears on every page. ---- ?>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "HomeAndConstructionBusiness",
  "name": "World Deal Technical Services",
  "url": "https://www.worlddealtechnicalservices.com/",
  "telephone": "+971565100471",
  "email": "info@worlddealtechnicalservices.ae",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "YOUR STREET / UNIT",
    "addressLocality": "YOUR EMIRATE e.g. Dubai",
    "addressCountry": "AE"
  },
  "areaServed": [
    "Dubai",
    "Abu Dhabi",
    "Sharjah"
  ],
  "openingHours": "Mo-Sa 08:00-18:00"
}
</script>

<?php
// ---- PART 2: your 13 FAQ questions. FAQ PAGE ONLY. ----
//
// This one is wrapped in an "if" on purpose. FAQ markup is only allowed on a
// page that really shows those questions to a visitor. Put it on all seven
// pages and Google can ignore all of it, or penalise the site. The line below
// means "only when the page being viewed is faq.php".
if (basename($_SERVER['SCRIPT_NAME'] ?? '') === 'faq.php'):
?>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "What services does World Deal Technical Services offer?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "We offer glass installation, aluminium doors and windows, office glass partitions, laminated glass, folding glass doors, glass stairs, bath glass, sign board design and installation, wooden doors and custom wooden interior work across Dubai and the UAE."
      }
    },
    {
      "@type": "Question",
      "name": "Do you provide free quotations?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. We offer free site visits and quotations for all projects across Dubai and the UAE. Contact us by phone, WhatsApp or via the contact form to schedule a consultation."
      }
    },
    {
      "@type": "Question",
      "name": "Do you install office glass partitions?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. We specialize in frameless and framed office glass partitions for corporate offices, co-working spaces, meeting rooms and commercial fit-outs across Dubai."
      }
    },
    {
      "@type": "Question",
      "name": "Can you install bathroom glass and shower partitions?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. We install bathroom glass, shower glass partitions and bath glass enclosures using strong tempered glass and neat stainless steel or chrome fittings for a clean modern bathroom look."
      }
    },
    {
      "@type": "Question",
      "name": "Can you make glass stairs and glass railings?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. We provide glass stairs, staircase glass railings and safety glass panels for homes, offices and premium interior projects throughout Dubai."
      }
    },
    {
      "@type": "Question",
      "name": "Do you provide pergola work in Dubai?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. We provide pergola work, aluminium pergolas and outdoor shade structure solutions for villas, balconies, patios and commercial outdoor spaces."
      }
    },
    {
      "@type": "Question",
      "name": "What types of aluminium work do you offer?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "We provide aluminium doors, aluminium windows, aluminium facades, curtain walls, aluminium partitions, aluminium pergolas, cladding and all types of custom aluminium fabrication and installation."
      }
    },
    {
      "@type": "Question",
      "name": "Can you design and install sign boards?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. We design, fabricate and install all types of sign boards including LED channel letters, 3D signs, acrylic signs, backlit signs, outdoor billboards and digital displays for shops, offices and commercial buildings."
      }
    },
    {
      "@type": "Question",
      "name": "Do you work in areas outside of Dubai?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. While our main operations are in Dubai, we also serve Sharjah, Abu Dhabi, Ajman and other Emirates across the UAE depending on the project size and requirements."
      }
    },
    {
      "@type": "Question",
      "name": "How long does a typical glass installation project take?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Project timelines vary by scope. A standard office glass partition can be completed in 1-3 days. Larger projects like full-floor fit-outs or villa glass installations may take 1-3 weeks. We always provide a clear timeline before starting work."
      }
    },
    {
      "@type": "Question",
      "name": "What makes World Deal Technical Services different?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "We combine 10+ years of experience with a dedicated team of skilled engineers and craftsmen, premium materials, competitive pricing and a commitment to on-time delivery. We treat every project with the same care and precision regardless of size."
      }
    },
    {
      "@type": "Question",
      "name": "How can I contact World Deal Technical Services?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "You can reach us by phone at +971 (56) 510 0471, by WhatsApp, by email, or through the contact form on our website. Our team responds promptly during business hours."
      }
    },
    {
      "@type": "Question",
      "name": "Still have questions?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Contact our team for a free consultation and quotation."
      }
    }
  ]
}
</script>
<?php endif; ?>
