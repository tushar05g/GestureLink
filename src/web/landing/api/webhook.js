const stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);
const admin = require('firebase-admin');

// Initialize Firebase Admin if it hasn't been initialized yet
if (!admin.apps.length) {
    try {
        admin.initializeApp({
            credential: admin.credential.cert({
                projectId: process.env.FIREBASE_PROJECT_ID,
                clientEmail: process.env.FIREBASE_CLIENT_EMAIL,
                // Replace escaped newlines so Vercel env variables work correctly
                privateKey: process.env.FIREBASE_PRIVATE_KEY?.replace(/\\n/g, '\n'),
            }),
            databaseURL: process.env.FIREBASE_DATABASE_URL
        });
    } catch (error) {
        console.error('Firebase initialization error', error.stack);
    }
}

// Needed to verify Stripe signatures correctly
export const config = {
    api: {
        bodyParser: false,
    },
};

// Helper function to read raw body for Stripe Signature Verification
async function buffer(readable) {
    const chunks = [];
    for await (const chunk of readable) {
        chunks.push(typeof chunk === 'string' ? Buffer.from(chunk) : chunk);
    }
    return Buffer.concat(chunks);
}

export default async function handler(req, res) {
    if (req.method !== 'POST') {
        return res.status(405).json({ error: 'Method Not Allowed' });
    }

    const buf = await buffer(req);
    const sig = req.headers['stripe-signature'];

    let event;

    try {
        // Verify the webhook signature using the raw body
        event = stripe.webhooks.constructEvent(buf, sig, process.env.STRIPE_WEBHOOK_SECRET);
    } catch (err) {
        console.error(`Webhook signature verification failed: ${err.message}`);
        return res.status(400).send(`Webhook Error: ${err.message}`);
    }

    // Handle successful checkout
    if (event.type === 'checkout.session.completed') {
        const session = event.data.object;
        
        // Stripe usually stores the customer's email in customer_details.email
        const customerEmail = session.customer_details?.email || session.customer_email;
        
        if (customerEmail) {
            console.log(`Payment successful for email: ${customerEmail}`);
            
            try {
                // We use base64 encoding for the email so it's a valid Firebase path (no dots or @)
                const safeEmailKey = Buffer.from(customerEmail).toString('base64');
                
                // Write to Realtime Database: /premium_users/<base64_email> = true
                const db = admin.database();
                await db.ref(`premium_users/${safeEmailKey}`).set({
                    isPremium: true,
                    purchasedAt: admin.database.ServerValue.TIMESTAMP
                });
                
                console.log('Successfully upgraded user in Firebase Realtime Database.');
            } catch (dbError) {
                console.error('Error writing to Firebase Realtime Database:', dbError);
                return res.status(500).json({ error: 'Database update failed' });
            }
        }
    }

    // Return 200 OK so Stripe knows we received the webhook
    res.status(200).json({ received: true });
}
