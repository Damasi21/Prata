document.addEventListener("DOMContentLoaded", () => {
    const passwordInput = document.getElementById("passwordInput");
    const passwordToggle = document.getElementById("passwordToggle");

    if (!passwordInput || !passwordToggle) return;

    passwordToggle.addEventListener("click", () => {
        const showing = passwordInput.type === "text";
        passwordInput.type = showing ? "password" : "text";
        passwordToggle.setAttribute("aria-label", showing ? "Mostrar senha" : "Ocultar senha");
    });
});
